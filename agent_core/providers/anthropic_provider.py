"""AnthropicProvider — native tool-calling, primary v1 target for PRIMARY (§4.1).

This is the ONLY cloud provider built in the first pass (engineering-spec §10).
OpenAI/Google adapters come later (design-doc Phase 4).

STATUS: stub. Implement against the Anthropic Messages API: translate
Addison's ToolDefinition list into `tools` blocks, map `tool_use` blocks back
to ToolCallRequest. The API key is fetched from the OS keychain at call time
via the shell (§5) — never cached in this process beyond a single request.
"""

from __future__ import annotations

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
)


class AnthropicProvider:
    def __init__(self, model: str = "claude-opus-4-8", api_key_getter=None) -> None:
        self._model = model
        self._api_key_getter = api_key_getter  # callable -> str, hits the shell/keychain

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True,
            max_context_tokens=200_000,
            supports_streaming=True,
            runs_off_device=False,
            vision=True,        # Claude models can analyze images
        )

    def send(self, messages: list[Message], tools: list) -> ModelResponse:
        # TODO(step 4): POST to the Anthropic Messages API and translate the response.
        raise NotImplementedError("Implement Anthropic Messages API call — spec §11 step 4.")
