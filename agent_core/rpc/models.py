"""model.* handlers — the catalog/role machinery behind the picker: the live
catalog load + provider reconnect, available roles, and explicit model+effort
selection (engineering-spec §7, §4.1.1, §6.8).

Local-model *setup* (model.startLocalSetup) lives in ``main.py`` instead: it is
OS/threading plumbing (disk/RAM probes, a background pull thread) whose probe
helpers tests monkeypatch on the ``agent_core.main`` namespace."""

from __future__ import annotations

from agent_core.models_catalog import (
    CloudModel,
    default_cloud_model,
    find_cloud_model,
    merge_catalogs,
)
from agent_core.providers.base import ModelRole
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import (
    _EFFORT_UNAVAILABLE_MESSAGE,
    _MODEL_UNAVAILABLE_MESSAGE,
    _SERVER_ERROR,
)


class ModelsMixin(ServerContext):
    # --- model roles ------------------------------------------------------
    def _maybe_load_catalogs(self) -> None:
        """First availableRoles: swap in the live Anthropic catalog (if a key is
        present) and reconnect every other provider the user connected in a previous
        launch, so the picker's union is whole again after a restart.

        Runs on the worker (never the read loop): the key probe, the Anthropic fetch,
        and each provider reconnect ping all do round-trips that block on frames the
        read loop must stay free to deliver. Failures are swallowed and leave the door
        open to retry (Anthropic) or to a manual reconnect (others)."""
        self._maybe_load_live_catalog()
        self._maybe_reconnect_saved_providers()

    def _maybe_load_live_catalog(self) -> None:
        """First availableRoles once a PRIMARY (Anthropic) key exists: swap the
        built-in fallback for the live list of every model the key can access, and
        register a provider per fetched entry so by-name picks resolve to it. Merges
        into the union (never clobbers other connected providers' models).

        Any failure — no key, offline, a bad response — keeps the fallback and leaves
        the door open to retry on a later availableRoles call (nothing is marked
        loaded). Registration is idempotent (dict replace), so repeated calls are safe."""
        if self._cloud_catalog_loaded or self._cloud_fetcher is None:
            return
        if not self._primary_key_available():
            return
        try:
            catalog = self._cloud_fetcher()
        except Exception:
            return   # keep the fallback; a later availableRoles may succeed
        if not catalog:
            return

        self._set_provider_models("anthropic", catalog)
        self._cloud_catalog_loaded = True
        if self._cloud_provider_factory is not None:
            for entry in catalog:
                self.model_router.register_primary_model(
                    entry.id, self._cloud_provider_factory(entry)
                )

    def _maybe_reconnect_saved_providers(self) -> None:
        """Reconnect the non-Anthropic providers persisted as connected in a prior
        launch (their keys are still in the keychain). One-shot per launch: a provider
        that can't be reached right now simply has no models until the user reconnects
        it from Settings. Anthropic is handled by the live-catalog path above."""
        if self._providers_reconnected or self._connect_provider is None or self._store is None:
            return
        self._providers_reconnected = True
        for cfg in self.store.list_provider_configs():
            provider_id = cfg["provider_id"]
            if provider_id == "anthropic" or not cfg["connected"]:
                continue
            try:
                models = self._connect_provider(provider_id, cfg["base_url"])
            except Exception:
                continue   # transient failure — user can reconnect manually
            self._set_provider_models(provider_id, models)

    def _set_provider_models(self, provider_id: str, models: list[CloudModel]) -> None:
        """Replace one provider's slice of the union picker menu with ``models``,
        keeping a single default across the whole union (merge_catalogs). Other
        providers' entries are untouched."""
        others = [m for m in self._cloud_catalog if m.provider != provider_id]
        self._cloud_catalog = merge_catalogs([others, list(models)])

    def _available_roles(self) -> dict:
        return {
            # SETUP_ASSISTANT is an internal onboarding role, never a user-selectable
            # option in the model picker (§4.1.1) — surface only PRIMARY/LOCAL.
            "roles": [
                role.value
                for role in self.model_router.available_roles()
                if role is not ModelRole.SETUP_ASSISTANT
            ],
            "localModels": self.model_router.available_local_models(),
            # The curated cloud menu the PRIMARY picker renders (§4.1.1, §6.8): each
            # entry carries its plain-language label/description and its "answer style"
            # (effort) choices — empty for a model with no effort control.
            "cloudModels": [model.to_wire() for model in self._cloud_catalog],
        }

    def _selection_error(
        self, role: ModelRole | None, model_id: str | None, effort: str | None
    ) -> str | None:
        """Validate an explicit model + effort pick for one message. Returns a plain
        error string, or None when the pick is allowed. A LOCAL pick names a local
        model and takes no effort; a PRIMARY (or default) pick names a cloud model
        and its effort must be one the model supports. An unknown id fails plainly
        HERE (early, explicit) rather than silently falling back at send time — the
        router keeps its own mid-conversation fallback as a separate safety net."""
        if role is ModelRole.LOCAL:
            if model_id is not None and model_id not in self.model_router.available_local_models():
                return _MODEL_UNAVAILABLE_MESSAGE
            if effort is not None:
                return _EFFORT_UNAVAILABLE_MESSAGE
            return None
        # PRIMARY, or role unset (which defaults to PRIMARY): a cloud pick.
        if model_id is not None and self._cloud_catalog:
            if find_cloud_model(self._cloud_catalog, model_id) is None:
                return _MODEL_UNAVAILABLE_MESSAGE
        if effort is not None:
            model = self._cloud_model_for(model_id)
            if model is None or effort not in model.supported_effort:
                return _EFFORT_UNAVAILABLE_MESSAGE
        return None

    def _cloud_model_for(self, model_id: str | None):
        """The catalog entry a cloud effort is validated against: the named model, or
        the catalog default when no model is named. None if there's no catalog or the
        named id isn't in it."""
        if not self._cloud_catalog:
            return None
        if model_id is None:
            return default_cloud_model(self._cloud_catalog)
        return find_cloud_model(self._cloud_catalog, model_id)

    def _handle_set_role(self, params: dict, request_id) -> None:
        role = self._role_from(params.get("role"))
        if params.get("role") and role is None:
            self._respond_error(request_id, _SERVER_ERROR, _MODEL_UNAVAILABLE_MESSAGE)
            return
        # An explicit pick may name WHICH model (a LOCAL model, item B, or a cloud
        # model, §6.8) and an "answer style" (effort). Validate both against the
        # configured pools/catalog so a stale/typo'd id or unsupported effort fails
        # plainly here rather than silently falling back at send time.
        model_id = params.get("modelId") or None
        effort = params.get("effort") or None
        error = self._selection_error(role, model_id, effort)
        if error is not None:
            self._respond_error(request_id, _SERVER_ERROR, error)
            return
        self._next_role = role
        self._next_model_name = model_id
        self._next_effort = effort
        self._respond(request_id, {"ok": True})
