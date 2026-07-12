"""ModelProvider protocol and shared model types — engineering-spec §3, §4.1.

The orchestrator is written entirely against ``ModelProvider`` and never
branches on the concrete provider. Capability differences are handled via
``capabilities()``, not ``isinstance`` checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


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


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"


class ModelProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    def send(self, messages: list[Message], tools: list["ToolDefinition"]) -> ModelResponse: ...  # noqa: F821
