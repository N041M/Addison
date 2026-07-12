"""OllamaProvider — local models, available post-setup (§4.1.2).

Talks to a local Ollama instance over HTTP (default http://127.0.0.1:11434).
Reuses OpenAIProvider's request/response translation where possible, since
Ollama exposes an OpenAI-compatible endpoint (design-doc §7.3.2).

Built LAST in v1 (engineering-spec §11 step 10) — it only makes sense once a
PRIMARY path already works, and its setup (hardware check → download → verify)
is a separate, user-initiated flow, never active during the Setup Assistant
conversation.

STATUS: stub. Note native_tool_calling is QUERIED from the loaded model's
metadata, not assumed; when absent, fall back to the prompt-based tool-call
parser (shared with SetupAssistantProvider), surfaced as "Basic tool support".
"""

from __future__ import annotations

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
)


class OllamaProvider:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434") -> None:
        self._model = model
        self._base_url = base_url

    def capabilities(self) -> ProviderCapabilities:
        # TODO(step 10): query Ollama model metadata for tool-calling support.
        return ProviderCapabilities(
            native_tool_calling=False,   # conservative default until queried
            max_context_tokens=8_192,
            supports_streaming=True,
            runs_off_device=True,
        )

    def send(self, messages: list[Message], tools: list) -> ModelResponse:
        # TODO(step 10): POST to Ollama's OpenAI-compatible endpoint; fall back
        # to prompt-based tool-call parsing when native tool calling is absent.
        raise NotImplementedError("Ollama integration is spec §11 step 10.")
