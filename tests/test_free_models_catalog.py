"""No cloud model claims to be free (step 4, contract D3, verification item 7).

Owner decision 2026-07-24: the "Answered with a free model" chip fires only for
free-by-construction models — local Ollama candidates (RoutingCandidate.local ⇒
free). No cloud CloudModel.free is ever True, so the chip only ever asserts a cost
fact Addison can actually establish. Google's free tier is surfaced as INFORMATION
(a Settings link, frontend copy), never a routing flag — which, on the backend,
means its catalog entries carry free=False like every other cloud model.
"""

from __future__ import annotations

from agent_core.models_catalog import (
    FALLBACK_CLOUD_MODELS,
    GOOGLE_CLOUD_MODELS,
    OPENAI_CLOUD_MODELS,
    load_cloud_catalog,
    static_catalog_for,
)


def _all_cloud_models():
    models = list(FALLBACK_CLOUD_MODELS)
    models += list(OPENAI_CLOUD_MODELS)
    models += list(GOOGLE_CLOUD_MODELS)
    models += static_catalog_for("openai")
    models += static_catalog_for("google")
    models += load_cloud_catalog()
    return models


def test_no_cloud_model_reports_free_true():
    offenders = [m.id for m in _all_cloud_models() if m.free]
    assert offenders == [], f"cloud models must never claim free=True: {offenders}"


def test_google_models_are_not_flagged_free():
    # Google's free tier is INFO only (a link), never a routing flag (D3).
    assert all(m.free is False for m in GOOGLE_CLOUD_MODELS)
    assert all(m.free is False for m in static_catalog_for("google"))
