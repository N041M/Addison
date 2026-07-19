"""ModelProvider protocol and shared model types — engineering-spec §3, §4.1.

The orchestrator is written entirely against ``ModelProvider`` and never
branches on the concrete provider. Capability differences are handled via
``capabilities()``, not ``isinstance`` checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


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
