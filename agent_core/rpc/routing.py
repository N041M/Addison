"""routing.* handlers + the per-turn chain builder (step 3, contract D1/D3/D7).

Two responsibilities:
  * ``_routing_chain`` — the callback the orchestrator receives (``routing_chain``):
    given the turn's role + explicit pick, it reads the active strategy, assembles
    the candidate universe from the router pools + the cloud catalog, and calls the
    PURE ``resolve_chain`` to order it. This is the ONLY place strategy, catalog and
    router meet; ``resolve_chain`` itself stays store-free.
  * ``routing.get`` / ``routing.set`` — the Settings surface. The strategy is a
    closed vocabulary (``quality_first | cost_first | local_only | custom`` — NO
    balanced, owner decision 2026-07-24). ``set`` validates, applies the D1 hook
    split, and persists via ``Store.set_settings`` in one commit.

Storage: two ``app_settings`` keys, captured by snapshots like any other config
(``scope.py`` already captures ``app_settings``) and NOT preserved on restore —
routing choices are reversible config, not floors.

camelCase mapper for its namespace at the wire boundary; settings and the strategy
slugs are snake_case, the wire keys camelCase (house style).
"""

from __future__ import annotations

import json

from agent_core.models_catalog import find_cloud_model
from agent_core.profiles import ProfileId
from agent_core.providers.base import ModelRole
from agent_core.providers.router import (
    CUSTOM,
    DEFAULT_ROUTING_STRATEGY,
    LOCAL_ONLY,
    QUALITY_FIRST,
    ROUTING_STRATEGIES,
    RoutingCandidate,
    resolve_chain,
)
from agent_core.rpc.base import ServerContext

# Settings keys (D1). Plain app_settings rows — a restore rolls them back with
# everything else; they are reversible config, not floors, and are NOT in
# scope._PRESERVED_SETTING_KEYS.
_ROUTING_STRATEGY_KEY = "routing_strategy"
_ROUTING_CUSTOM_CHAIN_KEY = "routing_custom_chain"

# Frozen refusals (D7). An unknown value changes nothing; a failed custom-chain
# snapshot changes nothing (user-authored order that exists nowhere else — same
# policy as a note/skill overwrite, [S1]).
_UNKNOWN_STRATEGY = "That isn't a way of picking models Addison recognises, so nothing was changed."
_UNKNOWN_CHAIN_MODEL = "That list includes a model Addison doesn't have, so nothing was changed."
_SNAPSHOT_FAILED = (
    "Addison couldn't save a restore point for your custom model order, so it didn't "
    "change anything. Try again in a moment."
)


class RoutingMixin(ServerContext):
    # --- stored config reads (coerced, never trusting the row) --------------
    def _routing_strategy(self) -> str:
        """The active strategy. An absent key resolves to quality_first — and so
        does any unknown value — so the no-key path and an explicit quality_first are
        one and the same ([MF-D])."""
        raw = self.store.get_setting(_ROUTING_STRATEGY_KEY, DEFAULT_ROUTING_STRATEGY)
        return raw if raw in ROUTING_STRATEGIES else DEFAULT_ROUTING_STRATEGY

    def _routing_custom_chain(self) -> list[str]:
        """The stored custom order as a list of model ids, or [] on absent/garbage."""
        raw = self.store.get_setting(_ROUTING_CUSTOM_CHAIN_KEY)
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        if not isinstance(parsed, list):
            return []
        return [x for x in parsed if isinstance(x, str)]

    def _model_label(self, model_id: str) -> str:
        """Human label for a model id — the catalog display name, else the id itself
        (locals are named by their id). Drives the answeredWith chip + fallback note."""
        if model_id and self._cloud_catalog:
            entry = find_cloud_model(self._cloud_catalog, model_id)
            if entry is not None:
                return entry.label
        return model_id or ""

    # --- the candidate universe (router pools + catalog metadata) -----------
    def _routing_candidates(self) -> list[RoutingCandidate]:
        """Every currently-available candidate across BOTH pools (D2). Cloud models
        take rank/free/provider from the catalog entry; a model with no catalog entry
        (defensive) carries unknown rank + a placeholder provider. [MF-E] all locals
        share provider_id 'ollama'."""
        candidates: list[RoutingCandidate] = []
        for mid in self.model_router.available_primary_models():
            entry = find_cloud_model(self._cloud_catalog, mid) if self._cloud_catalog else None
            if entry is not None:
                candidates.append(
                    RoutingCandidate(
                        model_id=mid, role=ModelRole.PRIMARY, provider_id=entry.provider,
                        quality_rank=entry.quality_rank, free=entry.free, local=False,
                    )
                )
            else:
                candidates.append(
                    RoutingCandidate(
                        model_id=mid, role=ModelRole.PRIMARY, provider_id="unknown",
                        quality_rank=None, free=False, local=False,
                    )
                )
        for mid in self.model_router.available_local_models():
            candidates.append(
                RoutingCandidate(
                    model_id=mid, role=ModelRole.LOCAL, provider_id="ollama",
                    quality_rank=None, free=True, local=True,
                )
            )
        return candidates

    def _routing_chain(
        self, requested_role: ModelRole | None, model_name: str | None
    ) -> list[RoutingCandidate] | None:
        """The orchestrator's ``routing_chain`` callback (D4). Returns the ordered
        fallback chain, or None to signal "no chain — use the single-provider path"
        (the onboarding relay, which is single, not metered, and never routed)."""
        if requested_role is ModelRole.SETUP_ASSISTANT:
            return None
        universe = self._routing_candidates()
        local = [c for c in universe if c.local]
        cloud = [c for c in universe if not c.local]
        local_ids = {c.model_id for c in local}
        strategy = self._routing_strategy()

        # A local-resolving turn NEVER falls back to the single-provider path: doing
        # so would let ``resolve(LOCAL, ...)`` fall through to the cloud PRIMARY, which
        # is exactly the leak local routing must not have. An empty local chain fails
        # plainly instead (and rpc/conversation.py already refuses local_only with an
        # empty pool before the turn ever runs).
        if requested_role is ModelRole.LOCAL:
            head = model_name or self.model_router.selected_local_model()
            return resolve_chain(LOCAL_ONLY, local, head)
        if strategy == LOCAL_ONLY:
            # Defence in depth — rpc/conversation.py forces the LOCAL role and refuses
            # a cloud pick before reaching here.
            head = model_name or self.model_router.selected_local_model()
            return resolve_chain(LOCAL_ONLY, universe, head)

        # An explicit per-message pick: it heads the chain and fall-forward stays in
        # its own class — a picked local never falls to cloud and vice versa ([S2]b).
        # Tail is quality order; the user chose the head, so the strategy's cost/free
        # preference does not override that choice. A LOCAL pick never single-paths
        # (same no-cloud-leak reason); a CLOUD pick with an empty pool does.
        if model_name is not None:
            if model_name in local_ids:
                return resolve_chain(QUALITY_FIRST, local, model_name)
            return resolve_chain(QUALITY_FIRST, cloud, model_name) or None

        # The routed default: today's resolution is the head, the strategy the tail.
        # An empty universe (no routing pools wired — the CLI/test single-provider
        # setup) returns None, so run_turn keeps today's resolution byte-for-byte.
        head = self.model_router.selected_primary_model()
        custom_order = self._routing_custom_chain()
        chain = resolve_chain(strategy, universe, head, custom_order=custom_order)
        # D3: custom-chain ids that have vanished since the list was saved are
        # skipped — with ONE plain note, because a fallback model the user chose
        # silently disappearing is exactly the quiet change the Activity Panel
        # exists to surface (post-build adversarial pass, 2026-07-24: the skip
        # shipped, the note did not).
        if strategy == CUSTOM and custom_order:
            present = {c.model_id for c in chain}
            gone = [m for m in custom_order if m not in present]
            if gone:
                names = ", ".join(gone)
                try:
                    self._emit_activity(
                        "routing",
                        f"Some models in your custom order aren't set up any more, "
                        f"so Addison skipped them: {names}.",
                    )
                except Exception:
                    pass
        return chain or None

    # --- RPC (D7) ----------------------------------------------------------
    def _routing_get(self) -> dict:
        """routing.get -> {strategy, availableStrategies, customChain, surface}.
        ``surface`` is derived from the active profile: Simple sees the two-way
        toggle, Developer/Custom the full picker + chain builder."""
        self._ensure_built()
        profile = self._active_profile
        surface = "full" if (profile is not None and profile.id is not ProfileId.SIMPLE) else "toggle"
        return {
            "strategy": self._routing_strategy(),
            "availableStrategies": list(ROUTING_STRATEGIES),
            "customChain": self._routing_custom_chain(),
            "surface": surface,
        }

    def _routing_set(self, params: dict) -> dict:
        """routing.set {strategy?, customChain?}. In order (D1/D7):

          1. validate the closed vocab + known ids — anything unknown refuses and
             NOTHING changes;
          2. hook split [S1]: a pure strategy change proceeds-with-warning if the
             snapshot fails (a recoverable enum); a customChain OVERWRITE REFUSES if
             the snapshot fails (user-authored content that exists nowhere else);
          3. persist both keys in ONE commit (Store.set_settings)."""
        self._ensure_built()
        strategy = params.get("strategy")
        custom_chain = params.get("customChain")

        if strategy is not None and strategy not in ROUTING_STRATEGIES:
            return {"ok": False, "error": _UNKNOWN_STRATEGY}
        if custom_chain is not None:
            if not isinstance(custom_chain, list) or not all(
                isinstance(x, str) for x in custom_chain
            ):
                return {"ok": False, "error": _UNKNOWN_CHAIN_MODEL}
            known = set(self.model_router.available_primary_models()) | set(
                self.model_router.available_local_models()
            )
            if any(mid not in known for mid in custom_chain):
                return {"ok": False, "error": _UNKNOWN_CHAIN_MODEL}

        if strategy is None and custom_chain is None:
            return {
                "ok": True,
                "strategy": self._routing_strategy(),
                "customChain": self._routing_custom_chain(),
            }

        # One snapshot for the whole change. Refuse ONLY when a customChain overwrite
        # is part of it and the snapshot failed; a strategy-only change proceeds with
        # the sticky warning _snapshot_auto set.
        snapshot_ok = self._snapshot_auto("routing_change")
        if custom_chain is not None and not snapshot_ok:
            return {"ok": False, "error": _SNAPSHOT_FAILED}

        values: dict[str, str] = {}
        if strategy is not None:
            values[_ROUTING_STRATEGY_KEY] = strategy
        if custom_chain is not None:
            values[_ROUTING_CUSTOM_CHAIN_KEY] = json.dumps(custom_chain)
        self.store.set_settings(values)
        return {
            "ok": True,
            "strategy": self._routing_strategy(),
            "customChain": self._routing_custom_chain(),
        }
