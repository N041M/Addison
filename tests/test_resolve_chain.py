"""Step-3 stage 1 — ``resolve_chain`` ordering (contract D2/D3, [MF-D]/[N-c]).

Pure-function tests: no store, no providers, no network. Each pins one ordering
rule and goes red if that rule's line is reverted.
"""

from __future__ import annotations

from agent_core.providers.base import ModelRole
from agent_core.providers.router import (
    COST_FIRST,
    CUSTOM,
    LOCAL_ONLY,
    QUALITY_FIRST,
    RoutingCandidate,
    resolve_chain,
)


def _cloud(model_id, rank, provider_id="anthropic"):
    return RoutingCandidate(
        model_id=model_id, role=ModelRole.PRIMARY, provider_id=provider_id,
        quality_rank=rank, free=False, local=False,
    )


def _local(model_id):
    return RoutingCandidate(
        model_id=model_id, role=ModelRole.LOCAL, provider_id="ollama",
        quality_rank=None, free=True, local=True,
    )


def _ids(chain):
    return [c.model_id for c in chain]


# --- quality_first ----------------------------------------------------------
def test_quality_first_head_is_selected_primary_even_when_weak():
    # FREEZE (Verification #4): the head is today's resolution regardless of rank.
    opus = _cloud("opus", 10)
    sonnet = _cloud("sonnet", 30)
    haiku = _cloud("haiku", 60)
    chain = resolve_chain(QUALITY_FIRST, [opus, sonnet, haiku], head_model_id="haiku")
    assert _ids(chain)[0] == "haiku"
    # tail is the others by quality asc (strongest first)
    assert _ids(chain) == ["haiku", "opus", "sonnet"]


def test_quality_first_unknown_rank_sorts_behind_head_ahead_of_ranked():
    # [N-c]/D2: a rank-None candidate (a just-released model) is never demoted
    # below known-weak ranked ones — it sits directly behind the head.
    head = _cloud("opus", 10)
    ranked = _cloud("sonnet", 30)
    unknown = _cloud("brand-new", None)
    chain = resolve_chain(QUALITY_FIRST, [head, ranked, unknown], head_model_id="opus")
    assert _ids(chain) == ["opus", "brand-new", "sonnet"]


def test_quality_first_locals_last():
    head = _cloud("opus", 10)
    local = _local("llama3")
    ranked = _cloud("sonnet", 30)
    chain = resolve_chain(QUALITY_FIRST, [head, local, ranked], head_model_id="opus")
    assert _ids(chain) == ["opus", "sonnet", "llama3"]


def test_quality_first_vanished_head_falls_through_gracefully():
    a = _cloud("a", 30)
    b = _cloud("b", 10)
    chain = resolve_chain(QUALITY_FIRST, [a, b], head_model_id="gone")
    # No forced head; pure quality order (strongest first).
    assert _ids(chain) == ["b", "a"]


# --- cost_first -------------------------------------------------------------
def test_cost_first_free_and_local_precede_the_paid_segment():
    opus = _cloud("opus", 10)
    local = _local("llama3")
    sonnet = _cloud("sonnet", 30)
    chain = resolve_chain(COST_FIRST, [opus, local, sonnet], head_model_id="opus")
    # free+local first; then selected_primary heads the PAID segment.
    assert _ids(chain) == ["llama3", "opus", "sonnet"]


def test_cost_first_paid_head_is_selected_primary():
    opus = _cloud("opus", 10)
    haiku = _cloud("haiku", 60)
    chain = resolve_chain(COST_FIRST, [opus, haiku], head_model_id="haiku")
    # No free models -> paid segment only, headed by the selected primary (haiku).
    assert _ids(chain)[0] == "haiku"


# --- local_only -------------------------------------------------------------
def test_local_only_returns_only_locals():
    opus = _cloud("opus", 10)
    l1 = _local("llama3")
    l2 = _local("mistral")
    chain = resolve_chain(LOCAL_ONLY, [opus, l1, l2], head_model_id="opus")
    assert _ids(chain) == ["llama3", "mistral"]
    assert all(c.local for c in chain)


def test_local_only_empty_when_no_locals():
    assert resolve_chain(LOCAL_ONLY, [_cloud("opus", 10)], head_model_id="opus") == []


def test_local_only_puts_the_picked_local_first():
    l1 = _local("llama3")
    l2 = _local("mistral")
    # An explicit local pick heads the chain even under local_only.
    chain = resolve_chain(LOCAL_ONLY, [l1, l2], head_model_id="mistral")
    assert _ids(chain) == ["mistral", "llama3"]


# --- custom -----------------------------------------------------------------
def test_custom_follows_the_stored_order():
    a = _cloud("a", 10)
    b = _cloud("b", 30)
    c = _cloud("c", 60)
    chain = resolve_chain(CUSTOM, [a, b, c], head_model_id="a", custom_order=["c", "a", "b"])
    assert _ids(chain) == ["c", "a", "b"]


def test_custom_skips_vanished_ids():
    a = _cloud("a", 10)
    b = _cloud("b", 30)
    chain = resolve_chain(CUSTOM, [a, b], head_model_id="a", custom_order=["gone", "b", "a"])
    assert _ids(chain) == ["b", "a"]


def test_custom_empty_falls_back_to_quality_first():
    a = _cloud("a", 30)
    b = _cloud("b", 10)
    chain = resolve_chain(CUSTOM, [a, b], head_model_id="a", custom_order=[])
    # quality_first with head 'a', then the rest by rank.
    assert _ids(chain) == ["a", "b"]


def test_custom_all_vanished_falls_back_to_quality_first():
    a = _cloud("a", 30)
    b = _cloud("b", 10)
    chain = resolve_chain(CUSTOM, [a, b], head_model_id="a", custom_order=["x", "y"])
    assert _ids(chain) == ["a", "b"]


# --- [MF-D] one resolution path ---------------------------------------------
def test_unknown_strategy_resolves_identically_to_quality_first():
    # An absent routing key resolves through quality_first — the same code path,
    # not a special no-key branch. A round-tripped toggle cannot diverge.
    pool = [_cloud("opus", 10), _cloud("sonnet", 30), _local("llama3")]
    assert _ids(resolve_chain("nonsense", pool, head_model_id="opus")) == _ids(
        resolve_chain(QUALITY_FIRST, pool, head_model_id="opus")
    )
    assert _ids(resolve_chain(None, pool, head_model_id="opus")) == _ids(  # type: ignore[arg-type]
        resolve_chain(QUALITY_FIRST, pool, head_model_id="opus")
    )
