"""SetupAssistantProvider — onboarding-only free relay (design-doc §7.5.1, §4.6).

Fills the SETUP_ASSISTANT role ONLY. Calls the external serverless relay; never
holds a real key locally (relay keys live server-side, out of this repo's trust
boundary entirely — §8.4). Degrades to a prompt-based tool-call parser if the
underlying free model lacks native function-calling — which is fine here, since
the Setup Assistant's job is guiding configuration, not agentic tool use.

Requests are signed with the device private key (from the OS keychain via the
shell, §5) and carry the signed device token. The relay enforces the one-time
setup-session cap server-side.

Built after the core loop works end-to-end (engineering-spec §11 step 9).

STATUS: stub.
"""

from __future__ import annotations

from agent_core.providers.base import (
    Message,
    ModelResponse,
    ProviderCapabilities,
)


class SetupAssistantProvider:
    def __init__(self, relay_url: str, device_signer=None) -> None:
        self._relay_url = relay_url
        self._device_signer = device_signer  # signs requests with the device key

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=False,   # small free models; prompt-based parsing fallback
            max_context_tokens=8_192,
            supports_streaming=True,
            runs_off_device=False,
        )

    def send(self, messages: list[Message], tools: list) -> ModelResponse:
        # TODO(step 9): signed request to the relay; handle at-cap wrap-up response.
        raise NotImplementedError("Setup Assistant relay is spec §11 step 9.")
