"""ModelRouter — resolves which provider handles a given request (§4.1.1).

The core structural change enabling "multiple models for different things":
PRIMARY and LOCAL can both be configured and reachable within the same running
session, and which one handles a message is a per-request decision, not a
session-wide setting.

Two axes of selection:
  - role   : PRIMARY | LOCAL | SETUP_ASSISTANT (which *job*)
  - model  : within LOCAL, *which* of several configured local models (item B).
             A user can run e.g. a 14B vision model and an 8B text model at once
             and pick per message; a Routine step can pin one by name.

v1 routing is EXPLICIT only — a user toggle per message, or a Routine step's
model_role/model_name. Automatic task-based routing (picking the model from task
difficulty / required capability like vision) is deliberately deferred to v2
(§4.1.1): v2 will call `resolve()` with a `model_name` it chooses, so the
substrate here is exactly what it builds on — only the *decision* is deferred.
No hidden auto-routing in v1.
"""

from __future__ import annotations

from agent_core.providers.base import ModelProvider, ModelRole


class ModelRouter:
    def __init__(
        self,
        configured: dict[ModelRole, ModelProvider],
        local_models: dict[str, ModelProvider] | None = None,
        selected_local: str | None = None,
        primary_models: dict[str, ModelProvider] | None = None,
        selected_primary: str | None = None,
    ):
        # Single-provider roles (PRIMARY, SETUP_ASSISTANT — and optionally a lone
        # LOCAL) live in `configured`. When several local models are configured,
        # they live in `local_models` keyed by model name, with one selected.
        self._configured = configured
        self._local_models = dict(local_models or {})
        self._selected_local = selected_local or next(iter(self._local_models), None)
        # PRIMARY mirrors LOCAL: several *cloud* models (the curated catalog,
        # models_catalog.py) can be configured at once and picked per message by
        # name (§6.8 — the cascade substrate extends named selection to the cloud).
        # `configured[PRIMARY]` remains the default/fallback; the pool holds every
        # nameable cloud model. All selection stays explicit — no auto-routing.
        self._primary_models = dict(primary_models or {})
        self._selected_primary = selected_primary or next(iter(self._primary_models), None)

    def resolve(
        self, requested_role: ModelRole | None = None, model_name: str | None = None
    ) -> ModelProvider:
        """Returns the provider for a request. ``requested_role`` is an explicit
        override (UI selection or a Routine step's model_role, §6.2); if None,
        defaults to PRIMARY. ``model_name`` selects among several LOCAL models
        (item B); if None, uses the currently selected local model. Falls back to
        whatever IS configured rather than erroring mid-conversation — surface a
        plain-language notice in the Activity Panel instead.

        NOTE: in v1 ``model_name`` is only ever passed from an explicit user/Routine
        choice. v2 auto-routing is the only thing that will pass a model_name Addison
        picked itself, and even then it stays overridable and visible (§4.1.1)."""
        role = requested_role or ModelRole.PRIMARY
        if role is ModelRole.LOCAL and self._local_models:
            name = model_name or self._selected_local
            if name is not None and name in self._local_models:
                return self._local_models[name]
            # else fall through to a single LOCAL provider in `configured`, if any
        if role is ModelRole.PRIMARY and self._primary_models:
            name = model_name or self._selected_primary
            if name is not None and name in self._primary_models:
                return self._primary_models[name]
            # An unknown explicit name (e.g. a Routine step pinning a model the user
            # has since reconfigured away) is NOT an error mid-conversation (§4.1.1):
            # fall through to the default/selected primary in `configured` below.
        if role in self._configured:
            return self._configured[role]
        if ModelRole.PRIMARY in self._configured:
            return self._configured[ModelRole.PRIMARY]
        if self._configured:
            return next(iter(self._configured.values()))
        if self._local_models and self._selected_local is not None:
            return self._local_models[self._selected_local]
        if self._primary_models and self._selected_primary is not None:
            return self._primary_models[self._selected_primary]
        raise RuntimeError("No model provider is configured.")

    def register(self, role: ModelRole, provider: ModelProvider) -> None:
        """Additive — used for the Setup Assistant → BYOK handoff (§4.6): a new
        DirectAPIProvider is registered under PRIMARY without disturbing others."""
        self._configured[role] = provider

    def register_local_model(self, model_name: str, provider: ModelProvider) -> None:
        """Add a local model to the LOCAL pool (item B). The first one added
        becomes the selected default."""
        self._local_models[model_name] = provider
        if self._selected_local is None:
            self._selected_local = model_name

    def register_primary_model(self, model_name: str, provider: ModelProvider) -> None:
        """Add a cloud model to the PRIMARY pool (§6.8). Mirrors ``register_local_model``:
        the first one added becomes the selected default. main.py registers one
        ``AnthropicProvider`` per catalog entry, all sharing the same key-getter."""
        self._primary_models[model_name] = provider
        if self._selected_primary is None:
            self._selected_primary = model_name

    def unregister_primary_model(self, model_name: str) -> None:
        """Remove a cloud model from the PRIMARY pool (provider.disconnect). If it was
        the selected default, fall back to whatever remains. Unknown names are a no-op —
        disconnecting is idempotent."""
        self._primary_models.pop(model_name, None)
        if self._selected_primary == model_name:
            self._selected_primary = next(iter(self._primary_models), None)

    def available_primary_models(self) -> list[str]:
        """The nameable cloud models — the ids the picker sends back as ``modelId``
        when the PRIMARY role is selected (§4.1.1)."""
        return list(self._primary_models)

    def select_local_model(self, model_name: str) -> None:
        """Set the local model the per-message Local picker resolves to."""
        if model_name not in self._local_models:
            raise KeyError(f"Local model '{model_name}' is not configured.")
        self._selected_local = model_name

    def available_local_models(self) -> list[str]:
        """Drives the Local model dropdown in the frontend (item B)."""
        return list(self._local_models)

    def available_roles(self) -> list[ModelRole]:
        """Drives the frontend's model-role selector — only roles the user has
        actually configured. LOCAL appears once at least one local model is
        downloaded and verified (§4.1.2), whether it sits in `configured` or the
        `local_models` pool."""
        roles = list(self._configured.keys())
        if self._local_models and ModelRole.LOCAL not in roles:
            roles.append(ModelRole.LOCAL)
        return roles
