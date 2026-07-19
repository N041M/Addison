"""provider.* handlers — the multi-provider connection surface (list / connect /
disconnect) plus the connection-status rollup stats.get renders (engineering-spec
§7, §4.1.1; owner decision 2026-07-18). Carries ONLY non-secret status/metadata —
never any key material (§8.3)."""

from __future__ import annotations

import time

from agent_core.models_catalog import PROVIDER_IDS, provider_label
from agent_core.providers.ollama_provider import is_running
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _GENERIC_TURN_ERROR


def _valid_http_url(url) -> bool:
    """A custom-server base URL is accepted only when it is an ``http://`` or
    ``https://`` URL with a host after the scheme. ``http://`` is deliberately
    permitted — a custom server is the ONE allowed plain-HTTP case (localhost/LAN
    model hosts). No other scheme (``file:``, ``ftp:``, …) is ever accepted."""
    if not isinstance(url, str):
        return False
    for scheme in ("http://", "https://"):
        if url.startswith(scheme) and len(url) > len(scheme):
            return True
    return False


class ProvidersMixin(ServerContext):
    def _connections(self, latency: list[dict]) -> list[dict]:
        """Ollama (probed live) + each connected cloud provider. Status/detail are
        plain strings; there is NEVER any key material in this payload (§8.3)."""
        conns: list[dict] = []
        try:
            ollama_up = is_running(self._ollama_base_url, self._ollama_client)
        except Exception:
            ollama_up = False
        conns.append(
            {
                "id": "ollama",
                "label": "Ollama · this computer",
                "status": "running" if ollama_up else "idle",
                "detail": "running" if ollama_up else "not running",
            }
        )
        latency_by_provider = {row["provider"]: row["ms"] for row in latency}
        stored = {c["provider_id"]: c for c in self.store.list_provider_configs()}
        for provider_id in PROVIDER_IDS:
            cfg = stored.get(provider_id)
            if cfg is not None:
                connected = cfg["connected"]
            else:
                connected = provider_id != "custom" and self._provider_key_present(provider_id)
            if not connected:
                continue
            ms = latency_by_provider.get(provider_id)
            label = provider_label(provider_id)
            conns.append(
                {
                    "id": provider_id,
                    "label": f"{label} API" if provider_id != "custom" else label,
                    "status": "reachable",
                    "detail": f"{ms} ms" if ms is not None else "connected",
                }
            )
        return conns

    # --- provider connections (multi-provider, §4.1.1) --------------------
    def _provider_key_present(self, provider_id: str) -> bool:
        probe = self._provider_key_probe
        if probe is None:
            return False
        try:
            return bool(probe(provider_id))
        except Exception:
            return False

    def _provider_list(self) -> dict:
        """provider.list -> {providers: [...]}. Carries ONLY non-secret status and
        metadata — NEVER any key material (invariant §8.3): id, plain label, whether
        it is connected, and (when known) the added date, custom base URL, and the
        last connect-check result.

        ``connected`` trusts a stored connection row exactly; only when there is NO
        row does it fall back to 'a key is already in the keychain' — that fallback
        exists so a legacy/migrated Anthropic key shows connected without a re-connect."""
        self._ensure_built()
        stored = {c["provider_id"]: c for c in self.store.list_provider_configs()}
        rows: list[dict] = []
        for provider_id in PROVIDER_IDS:
            cfg = stored.get(provider_id)
            if cfg is not None:
                connected = cfg["connected"]
            else:
                connected = provider_id != "custom" and self._provider_key_present(provider_id)
            row: dict = {
                "id": provider_id,
                "label": provider_label(provider_id),
                "connected": connected,
            }
            if cfg is not None:
                if cfg["added_at"] is not None:
                    row["addedAt"] = cfg["added_at"]
                if provider_id == "custom" and cfg["base_url"]:
                    row["baseUrl"] = cfg["base_url"]
                if cfg["last_check_ok"] is not None:
                    row["lastCheckOk"] = cfg["last_check_ok"]
            rows.append(row)
        return {"providers": rows}

    def _provider_connect(self, params: dict) -> dict:
        """provider.connect {provider, baseUrl?} -> {ok, error?}. The key was already
        stored by the Rust command; here the core pulls it from the keychain, makes ONE
        tiny validating request, and — on success — records metadata and folds the
        provider's models into the picker union. On failure it does NOT mark the provider
        connected (the card offers Remove to clear the stored key)."""
        self._ensure_built()
        provider_id = params.get("provider")
        base_url = (params.get("baseUrl") or "").strip() or None
        if provider_id not in PROVIDER_IDS:
            return {"ok": False, "error": "That provider isn't available."}
        if provider_id == "custom" and not _valid_http_url(base_url):
            return {
                "ok": False,
                "error": "Enter a web address that starts with http:// or https://.",
            }
        if self._connect_provider is None:
            return {"ok": False, "error": "Connecting a provider needs the desktop app."}
        try:
            models = self._connect_provider(provider_id, base_url)
        except RuntimeError as exc:
            # Provider errors already carry a plain, user-ready sentence. Record the
            # failed check WITHOUT marking connected, so provider.list shows it off.
            self.store.upsert_provider_config(
                provider_id, connected=False, base_url=base_url, last_check_ok=False
            )
            return {"ok": False, "error": str(exc)}
        except Exception:
            self.store.upsert_provider_config(
                provider_id, connected=False, base_url=base_url, last_check_ok=False
            )
            return {"ok": False, "error": _GENERIC_TURN_ERROR}
        self.store.upsert_provider_config(
            provider_id,
            connected=True,
            added_at=int(time.time()),
            base_url=base_url,
            last_check_ok=True,
        )
        self._set_provider_models(provider_id, models)
        return {"ok": True}

    def _provider_disconnect(self, params: dict) -> dict:
        """provider.disconnect {provider} -> {ok}. Forget the connection metadata and
        drop that provider's models from the picker union and the router pool. The key
        itself is removed separately by the Rust keychain command (the webview calls it)."""
        self._ensure_built()
        provider_id = params.get("provider")
        if provider_id not in PROVIDER_IDS:
            return {"ok": False, "error": "That provider isn't available."}
        self.store.delete_provider_config(provider_id)
        for model in [m for m in self._cloud_catalog if m.provider == provider_id]:
            self.model_router.unregister_primary_model(model.id)
        self._cloud_catalog = [m for m in self._cloud_catalog if m.provider != provider_id]
        return {"ok": True}
