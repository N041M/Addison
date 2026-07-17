"""DirectAPIProvider — generic BYOK wrapper (§4.1).

Parameterized by provider name + key-getter, used for "bring your own key". The
key is fetched from the OS keychain at call time via the shell, NEVER cached in
Agent Core memory longer than a single request (§5, §8.3) — this wrapper holds
only the *getter* (a callable), never key material, and delegates the actual
call to a per-instance provider adapter that fetches the key per ``send()``.

This is what a completed Setup Assistant → BYOK handoff registers under
ModelRole.PRIMARY (§4.6). Structurally, a user's own key can never reach the
Setup Assistant relay: that path has no key-getter at all.

Only Anthropic is built in the first pass (spec §10) — any other provider name
is a plain-language RuntimeError, not a silent stub, so a mis-configured BYOK
attempt tells the user what actually happened.
"""

from __future__ import annotations

from agent_core.providers.anthropic_provider import AnthropicProvider
from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
)

# OpenAI/Google adapters are explicitly NOT built yet (spec §10). Plain language,
# no jargon (CLAUDE.md) — names the limit and the one thing that works today.
_UNSUPPORTED_PROVIDER_MESSAGE = (
    "Only Anthropic API keys work right now. Other providers are coming later — "
    "add an Anthropic key in Settings to continue."
)


class DirectAPIProvider:
    def __init__(self, provider_name: str, model: str, api_key_getter=None, client=None) -> None:
        self._provider_name = provider_name   # 'anthropic' | 'openai' | ...
        self._model = model
        self._api_key_getter = api_key_getter  # callable -> str; hits the keychain per call
        self._client = client                  # test-injected httpx.Client, threaded to the adapter
        self._adapter = self._build_adapter()

    def _build_adapter(self):
        """Construct the underlying provider adapter for ``provider_name``.

        The adapter keeps the SAME ``api_key_getter`` so the key is fetched fresh
        per request inside it — never fetched or held here. Unknown names yield
        None; ``_require_adapter`` turns that into a plain error at use time."""
        if (self._provider_name or "").lower() == "anthropic":
            return AnthropicProvider(
                model=self._model, api_key_getter=self._api_key_getter, client=self._client
            )
        return None

    def _require_adapter(self):
        if self._adapter is None:
            raise RuntimeError(_UNSUPPORTED_PROVIDER_MESSAGE)
        return self._adapter

    def capabilities(self) -> ProviderCapabilities:
        return self._require_adapter().capabilities()

    def send(
        self, messages: list[Message], tools: list, effort: str | None = None
    ) -> ModelResponse:
        # Thread ``effort`` through to the underlying adapter (an AnthropicProvider
        # today), which honors it only for a model that supports it.
        return self._require_adapter().send(messages, tools, effort=effort)
