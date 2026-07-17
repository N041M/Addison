"""OpenAIProvider — native tool-calling, translates Addison's tool schema to
OpenAI's function-calling format (§4.1).

DEFERRED: not built in the first pass (engineering-spec §10 — Anthropic only).
Present now because OllamaProvider (§4.1.2) reuses this provider's
request/response translation against a different base URL.

STATUS: stub.
"""

from __future__ import annotations

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
)


class OpenAIProvider:
    def __init__(self, model: str, api_key_getter=None, base_url: str = "https://api.openai.com/v1") -> None:
        self._model = model
        self._api_key_getter = api_key_getter
        self._base_url = base_url

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=128_000,
            supports_streaming=True,
            runs_off_device=False,
            vision=True,        # modern GPT-class models can analyze images
        )

    def send(
        self, messages: list[Message], tools: list, effort: str | None = None
    ) -> ModelResponse:
        # TODO(Phase 4): chat.completions with function-calling translation.
        raise NotImplementedError("OpenAI adapter is Phase 4 — spec §10.")
