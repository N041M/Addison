"""ModelProvider protocol and shared model types — engineering-spec §3, §4.1.

The orchestrator is written entirely against ``ModelProvider`` and never
branches on the concrete provider. Capability differences are handled via
``capabilities()``, not ``isinstance`` checks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    # Type-only, erased at runtime — the module-boundary rule (no runtime
    # providers -> tools import; tests/test_module_boundaries.py) still holds,
    # the same stance the frontend takes with allowTypeImports on lib/parse.ts.
    from agent_core.tools.base import ToolDefinition

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
    send: Callable[[], httpx.Response], *, idempotent: bool, allow_retry: bool = True
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

    ``allow_retry=False`` disables the internal retry entirely. Providers pass
    it when the caller supplied a per-call deadline ([MF-A]): a deadline means
    the routing attempt loop is driving, and THAT loop is the retry mechanism —
    a hidden second attempt here would double the per-turn budget on a
    ConnectTimeout (up to 2× deadline + the sleep), which is exactly the hole
    the budget exists to close (post-build adversarial pass, 2026-07-24).
    Standalone calls (no deadline) keep today's one-retry robustness unchanged.
    """
    retryable = _IDEMPOTENT_ERRORS if idempotent else _CONNECT_ERRORS
    if not allow_retry:
        return send()  # the chain retries by advancing candidates, never here
    try:
        response = send()
    except retryable:
        time.sleep(_RETRY_SLEEP_SECONDS)
        return send()  # second and final attempt; a re-raise propagates to the caller
    if idempotent and response.status_code >= 500:
        time.sleep(_RETRY_SLEEP_SECONDS)
        return send()
    return response


# --- provider failure hierarchy (step 3 stage 0, contract §"Stage 0") --------
# Three named failures the attempt loop (orchestrator, D4) branches on. All three
# subclass RuntimeError so every existing ``except RuntimeError`` still catches
# them (behaviour freeze) and each carries the SAME plain user-facing message the
# provider raised before this split — the type is new, the wording is byte-identical.
#
# The distinction the loop cares about is ONLY "may I try the next candidate?":
#   * ProviderUnavailable  -> transient; the loop MAY walk the chain.
#   * ProviderRequestRejected / ProviderAuthFailed -> fail the turn immediately, no
#     walk (the next provider gets the same bad request / the same missing key).


class ProviderUnavailable(RuntimeError):
    """A transient failure — HTTP 429, any 5xx, or a network connect/read timeout.
    The attempt loop may fall forward to the next candidate on this (D4)."""


class ProviderRequestRejected(RuntimeError):
    """A 4xx other than 401/403/429 — the request itself is bad, so the next
    provider would reject it identically. The turn fails immediately (D4)."""


class ProviderAuthFailed(RuntimeError):
    """A 401/403, or a missing/malformed API key — a credential problem the chain
    cannot route around for THIS candidate. The turn fails immediately (D4)."""


def exception_for_http_status(status_code: int, message: str) -> RuntimeError:
    """Classify a >=400 status into the hierarchy, carrying the caller's own plain
    message unchanged. 401/403 -> auth; 429 or 5xx -> unavailable; every other 4xx
    -> rejected. The order matters: auth is checked before the 429/5xx band."""
    if status_code in (401, 403):
        return ProviderAuthFailed(message)
    if status_code == 429 or status_code >= 500:
        return ProviderUnavailable(message)
    return ProviderRequestRejected(message)


def effective_timeout(override: float | None, default: float) -> float:
    """The per-call timeout a provider actually uses ([MF-A]). ``None`` -> the
    provider's own default (freeze). A value only ever TIGHTENS the default, never
    extends it, so handing a provider the full remaining per-turn budget on a
    healthy first send resolves to exactly today's constant."""
    if override is None:
        return default
    return min(override, default)


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
    #
    # ``timeout`` ([MF-A]) is an optional per-call deadline in seconds the attempt
    # loop threads down so a single hanging candidate can never blow the per-turn
    # budget. None means "use my own default" — byte-identical to today. A provider
    # never EXTENDS past its own default; the override only ever tightens it
    # (``effective_timeout``), so passing the whole remaining budget on the first,
    # healthy send resolves to today's constant exactly.
    def send(
        self,
        messages: list[Message],
        tools: list["ToolDefinition"],
        effort: str | None = None,
        timeout: float | None = None,
    ) -> ModelResponse: ...
