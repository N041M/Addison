"""ModelRouter — resolves which provider handles a given request (§4.1.1).

The core structural change enabling "multiple models for different things":
PRIMARY and LOCAL can both be configured and reachable within the same running
session, and which one handles a message is a per-request decision, not a
session-wide setting.

v1 routing is EXPLICIT only (user toggle per message, or a Routine step's
model_role). Automatic task-based routing is deliberately out of scope (§4.1.1,
§10) — no hidden decisions about where a message goes.
"""

from __future__ import annotations

from agent_core.providers.base import ModelProvider, ModelRole


class ModelRouter:
    def __init__(self, configured: dict[ModelRole, ModelProvider]):
        self._configured = configured   # populated from provider_config at startup

    def resolve(self, requested_role: ModelRole | None = None) -> ModelProvider:
        """Returns the provider for a request. ``requested_role`` is an explicit
        override (UI selection or a Routine step's model_role, §6.2); if None,
        defaults to PRIMARY. Falls back to whichever role IS configured if the
        requested one isn't — never a hard error mid-conversation; surface a
        plain-language notice in the Activity Panel instead."""
        role = requested_role or ModelRole.PRIMARY
        if role in self._configured:
            return self._configured[role]
        # Fallback: prefer PRIMARY, else any configured role.
        if ModelRole.PRIMARY in self._configured:
            return self._configured[ModelRole.PRIMARY]
        if self._configured:
            return next(iter(self._configured.values()))
        raise RuntimeError("No model provider is configured.")

    def register(self, role: ModelRole, provider: ModelProvider) -> None:
        """Additive — used for the Setup Assistant → BYOK handoff (§4.6): a new
        DirectAPIProvider is registered under PRIMARY without disturbing others."""
        self._configured[role] = provider

    def available_roles(self) -> list[ModelRole]:
        """Drives the frontend's model-role selector — only roles the user has
        actually configured. LOCAL only appears once a local model is
        downloaded and verified (§4.1.2)."""
        return list(self._configured.keys())
