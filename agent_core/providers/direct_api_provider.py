"""DirectAPIProvider — generic BYOK wrapper (§4.1).

Parameterized by provider name + key, used for "bring your own key". The key is
fetched from the OS keychain at call time via the shell, NEVER cached in Agent
Core memory longer than a single request (§5, §8.3).

This is what a completed Setup Assistant → BYOK handoff registers under
ModelRole.PRIMARY (§4.6). Structurally, a user's own key can never reach the
Setup Assistant relay.

STATUS: stub. Dispatches to the matching underlying provider adapter by name.
"""

from __future__ import annotations

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
)


class DirectAPIProvider:
    def __init__(self, provider_name: str, model: str, api_key_getter=None) -> None:
        self._provider_name = provider_name   # 'anthropic' | 'openai' | ...
        self._model = model
        self._api_key_getter = api_key_getter  # callable -> str, hits the shell/keychain per call

    def capabilities(self) -> ProviderCapabilities:
        # TODO: delegate to the underlying adapter's capabilities().
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=True,
            runs_off_device=False,
        )

    def send(self, messages: list[Message], tools: list) -> ModelResponse:
        # TODO(step 4+): fetch key via getter, dispatch to the named adapter, drop key after.
        raise NotImplementedError("BYOK dispatch — spec §11 step 4+.")
