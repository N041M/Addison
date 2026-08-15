"""Every model was one rank, so routing had one tier (owner report, 2026-08-12).

THE BUG. `models_catalog` matched a live id against its curated tables by EXACT
string, and anything else took ``_UNRANKED_QUALITY_RANK``. Providers list dated
builds (`claude-sonnet-5-20260203`), previews (`gemini-3-pro-preview`) and whole
generations the table predates, so in practice almost every row landed on the one
rank — and quality-first / cost-first, which order by rank, degraded to insertion
order. Anthropic was worse still: its live entries were built by
``_parse_model_entry``, which set no rank at all, so Opus, Sonnet and Haiku were
one undifferentiated bucket.

WHAT HOLDS NOW, in the order the resolver tries it: exact curated id, then the
curated entry a dated/pinned/preview id is a build OF (rank only — the label stays
the raw id), then family+size inference, then unranked. None of it may resurrect a
model the provider did not list; that is the 404 bug next door
(`test_live_model_registration.py`) and it stays closed.
"""

from __future__ import annotations

from agent_core.models_catalog import (
    FALLBACK_CLOUD_MODELS,
    GOOGLE_CLOUD_MODELS,
    OPENAI_CLOUD_MODELS,
    _infer_quality_rank,
    _UNRANKED_QUALITY_RANK,
    catalog_from_live_ids,
    find_cloud_model,
    quality_rank_for_live_id,
)


def _rank(provider: str, model_id: str) -> int:
    return quality_rank_for_live_id(provider, model_id)


# --- the regression itself ---------------------------------------------------


def test_a_realistic_live_list_produces_more_than_one_rank():
    """The whole report in one assertion. These are the shapes providers actually
    serve today: dated Anthropic builds, a Gemini generation newer than the table,
    an OpenAI mini. Before the fix every one of them was rank 80."""
    anthropic = catalog_from_live_ids(
        "anthropic",
        ["claude-opus-4-8-20260115", "claude-sonnet-5-20260203", "claude-haiku-4-5"],
    )
    google = catalog_from_live_ids("google", ["gemini-3.5-pro", "gemini-3.5-flash"])
    openai = catalog_from_live_ids("openai", ["gpt-5", "gpt-5-mini"])

    ranks = [m.quality_rank for m in anthropic + google + openai]
    assert None not in ranks
    assert len(set(ranks)) > 1, f"the rank system is flat again: {ranks}"
    # ...and not merely "more than one": each provider's own list is ordered.
    anthropic_ranks = [_rank("anthropic", m.id) for m in anthropic]
    assert anthropic_ranks == sorted(anthropic_ranks)
    assert _rank("google", google[0].id) < _rank("google", google[1].id)
    assert _rank("openai", openai[0].id) < _rank("openai", openai[1].id)


def test_the_old_exact_match_would_have_flattened_this_list():
    """Guards the fix rather than the symptom: not one of these ids is in a curated
    table, so exact matching alone gives them all the same number."""
    ids = ["claude-opus-4-8-20260115", "claude-haiku-4-5-20251001", "gemini-3.5-flash"]
    for mid in ids:
        assert not any(
            entry.id == mid
            for entry in FALLBACK_CLOUD_MODELS + GOOGLE_CLOUD_MODELS + OPENAI_CLOUD_MODELS
        )
    ranked = {
        _rank("anthropic", ids[0]), _rank("anthropic", ids[1]), _rank("google", ids[2])
    }
    assert len(ranked) == 3


# --- step 1: a dated build is the model it is a build of ---------------------


def test_a_dated_variant_takes_its_curated_rank():
    curated = find_cloud_model(list(FALLBACK_CLOUD_MODELS), "claude-sonnet-5")
    assert curated is not None and curated.quality_rank is not None
    assert _rank("anthropic", "claude-sonnet-5-20260203") == curated.quality_rank
    assert _rank("anthropic", "claude-sonnet-5-latest") == curated.quality_rank


def test_a_pinned_or_preview_variant_takes_its_curated_rank():
    pro = find_cloud_model(list(GOOGLE_CLOUD_MODELS), "gemini-2.5-pro")
    assert pro is not None
    assert _rank("google", "gemini-2.5-pro-preview") == pro.quality_rank
    assert _rank("google", "gemini-2.5-pro-001") == pro.quality_rank


def test_a_dated_variant_keeps_the_raw_id_as_its_label():
    """Rank is improved; the NAME is not invented. A provider can list the base and
    a dated build side by side, and two rows both reading "Claude Sonnet 5" would
    be a picker asking somebody to choose between identical-looking things."""
    models = catalog_from_live_ids("anthropic", ["claude-sonnet-5-20260203"])
    assert [m.label for m in models] == ["claude-sonnet-5-20260203"]


def test_normalization_never_resurrects_an_id_the_provider_did_not_list():
    """The 404 bug's rule is untouched: matching a variant improves a NUMBER, it
    never adds a model. Only the listed preview comes back."""
    listed = catalog_from_live_ids("google", ["gemini-3-pro-preview"])
    assert [m.id for m in listed] == ["gemini-3-pro-preview"]
    assert all(m.id != "gemini-3-pro" for m in listed)


# --- step 2: exact curation still wins ---------------------------------------


def test_an_exact_curated_rank_beats_family_inference():
    """gpt-4o and gpt-4.1 are the same generation and not the same model — the
    formula cannot tell them apart and hand knowledge can, so the table wins."""
    gpt4o = find_cloud_model(list(OPENAI_CLOUD_MODELS), "gpt-4o")
    assert gpt4o is not None and gpt4o.quality_rank == 35
    assert _infer_quality_rank("gpt-4o") != 35
    assert _rank("openai", "gpt-4o") == 35


def test_every_curated_rank_agrees_with_the_family_formula():
    """The trap this rule exists to stop: a curated rank BETTER than the formula's
    would leave the next generation — which only the formula can rank — sorting
    behind a hand-written entry forever. Worse is allowed (hand knowledge); better
    is not."""
    for entry in FALLBACK_CLOUD_MODELS + OPENAI_CLOUD_MODELS + GOOGLE_CLOUD_MODELS:
        inferred = _infer_quality_rank(entry.id)
        assert entry.quality_rank is not None
        if inferred is not None:
            assert entry.quality_rank >= inferred, (
                f"{entry.id} is hand-ranked {entry.quality_rank}, ahead of the formula's "
                f"{inferred} — a later generation of the same family would sort behind it"
            )


# --- step 3: family inference ------------------------------------------------


def test_a_plausible_future_model_ranks_inside_its_family():
    """A generation that arrives after this commit still sorts sensibly: bigger
    beats smaller within a generation, and a later generation beats an earlier one
    of the same size — but a size boundary is never crossed by version alone."""
    assert _rank("google", "gemini-4-pro") < _rank("google", "gemini-4-flash")
    assert _rank("google", "gemini-4-flash") < _rank("google", "gemini-4-flash-lite")
    assert _rank("google", "gemini-4-pro") < _rank("google", "gemini-3-pro")
    # ...and no number of generations promotes a flash past a pro.
    assert _rank("google", "gemini-9-flash") > _rank("google", "gemini-2.5-pro")

    assert _rank("anthropic", "claude-opus-6") < _rank("anthropic", "claude-sonnet-6")
    assert _rank("anthropic", "claude-sonnet-6") < _rank("anthropic", "claude-haiku-6")
    assert _rank("anthropic", "claude-opus-6") < _rank("anthropic", "claude-opus-4-8")
    # The older word order is the same family read the same way.
    assert _rank("anthropic", "claude-3-5-sonnet-20241022") > _rank("anthropic", "claude-sonnet-5")

    assert _rank("openai", "gpt-6") < _rank("openai", "gpt-6-mini")
    assert _rank("openai", "gpt-6-mini") < _rank("openai", "gpt-6-nano")
    assert _rank("openai", "o5") < _rank("openai", "o5-mini")


def test_an_unrecognised_name_stays_unranked_rather_than_guessed():
    """A wrong confident rank is worse than none: the router sorts unknown-rank
    models AHEAD of every ranked one, so an unrecognised model is favoured, never
    buried. Inference that recognises nothing must say so."""
    for mid in ("some-model-nobody-knows", "mystery-1", "gemini-embedded-thing"):
        assert _infer_quality_rank(mid) is None, mid
    assert _rank("google", "some-model-nobody-knows") == _UNRANKED_QUALITY_RANK
    assert _rank("custom", "whatever-7b") == _UNRANKED_QUALITY_RANK


def test_a_custom_server_gets_no_curated_table_but_still_reads_family_names():
    """An OpenAI-compatible proxy serves the same NAMES, so inference is id-based
    rather than provider-based; there is simply no curated table to consult."""
    assert _rank("custom", "claude-opus-4-8") == _infer_quality_rank("claude-opus-4-8")
