"""Tests for the context-budget decision logic (agent_core/context_budget.py,
engineering-spec §4.8).

This is the corruptible part of continuation: a wrong cut does not raise, it
produces a history the provider rejects or a chat that lost its own middle. So
the cut tests are written against the REAL message shape from providers/base.py
(``Message`` with ``role`` / ``tool_call_id`` / ``tool_calls`` /
``past_tool_calls``) rather than a convenient stand-in, and one of them fails
outright if a naive message-count index is ever used instead of a turn boundary.
"""

from __future__ import annotations

import random

import pytest

from agent_core.context_budget import (
    DEFAULT_THRESHOLD_FRACTION,
    KEEP_RECENT_TURNS,
    assess_budget,
    choose_cut_point,
    is_legal_cut,
    turn_start_indices,
)
from agent_core.providers.base import Message, ToolCallRequest


# --------------------------------------------------------------------------
# helpers that build histories in the shape orchestrator.Conversation appends
# --------------------------------------------------------------------------


def _call(n: object) -> ToolCallRequest:
    return ToolCallRequest(id=f"call_{n}", tool_id="calculator", args={})


def _plain_turn(n: int) -> list[Message]:
    return [
        Message(role="user", content=f"question {n}"),
        Message(role="assistant", content=f"answer {n}"),
    ]


def _tool_turn(n: int, calls: int = 1, past: bool = False) -> list[Message]:
    """A turn where the assistant used tools: user, assistant carrying the
    tool_use blocks, one tool message per call, then the closing assistant text.
    ``past=True`` puts the requests in ``past_tool_calls``, which is what
    conversation.load rebuilds for a reopened chat."""
    requests = [_call(f"{n}_{i}") for i in range(calls)]
    assistant = (
        Message(role="assistant", content="", past_tool_calls=requests)
        if past
        else Message(role="assistant", content="", tool_calls=requests)
    )
    out = [Message(role="user", content=f"question {n}"), assistant]
    out += [
        Message(role="tool", content="ok", tool_call_id=r.id) for r in requests
    ]
    out.append(Message(role="assistant", content=f"answer {n}"))
    return out


# --------------------------------------------------------------------------
# threshold
# --------------------------------------------------------------------------


def test_threshold_under_is_not_over():
    a = assess_budget(1_000, 100_000)
    assert a.known and not a.over_threshold
    assert a.threshold_tokens == 70_000


def test_threshold_over_is_over():
    a = assess_budget(80_000, 100_000)
    assert a.known and a.over_threshold


def test_threshold_exactly_at_the_line_counts_as_over():
    a = assess_budget(70_000, 100_000)
    assert a.known and a.over_threshold


def test_threshold_default_fraction_is_seventy_percent():
    assert DEFAULT_THRESHOLD_FRACTION == pytest.approx(0.70)


def test_threshold_fraction_is_honoured_when_passed():
    a = assess_budget(50_000, 100_000, threshold_fraction=0.4)
    assert a.threshold_tokens == 40_000 and a.over_threshold


def test_provider_reporting_no_maximum_cannot_tell():
    a = assess_budget(999_999, None)
    assert a.cannot_tell and not a.known
    assert not a.over_threshold  # never a guess
    assert a.threshold_tokens is None


@pytest.mark.parametrize("limit", [0, -1, -100_000])
def test_nonsense_limits_cannot_tell(limit: int):
    a = assess_budget(10_000, limit)
    assert a.cannot_tell and not a.over_threshold


def test_negative_usage_cannot_tell():
    a = assess_budget(-5, 100_000)
    assert a.cannot_tell and not a.over_threshold


def test_zero_usage_is_a_real_answer_of_not_over():
    a = assess_budget(0, 100_000)
    assert a.known and not a.over_threshold


@pytest.mark.parametrize("fraction", [0.0, -0.5, 1.5])
def test_impossible_fractions_are_rejected(fraction: float):
    with pytest.raises(ValueError):
        assess_budget(10, 100, threshold_fraction=fraction)


def test_assessment_is_frozen():
    a = assess_budget(1, 100)
    with pytest.raises(Exception):
        a.over_threshold = True  # type: ignore[misc]


# --------------------------------------------------------------------------
# cut points
# --------------------------------------------------------------------------


def test_clean_multi_turn_conversation_cuts_at_a_turn_start():
    messages = [m for n in range(10) for m in _plain_turn(n)]
    cut = choose_cut_point(messages)
    assert cut.found and cut.index is not None
    assert messages[cut.index].role == "user"
    assert cut.kept_turns == KEEP_RECENT_TURNS
    assert turn_start_indices(messages)[-KEEP_RECENT_TURNS] == cut.index


def test_cut_never_splits_tool_use_from_its_tool_results():
    """Every turn uses tools, so the naive "last N messages" index lands between
    an assistant tool_use and its tool_result. This test FAILS if that index is
    used: the assistant before the cut must not be one that requested tools, and
    the tail must open with a user message."""
    messages = [m for n in range(8) for m in _tool_turn(n, calls=2)]
    cut = choose_cut_point(messages)
    assert cut.found and cut.index is not None

    naive_index = len(messages) - KEEP_RECENT_TURNS  # message count, not turns
    assert cut.index != naive_index
    assert messages[naive_index].role != "user"  # the naive index really is illegal
    assert not is_legal_cut(messages, naive_index)

    assert messages[cut.index].role == "user"
    assert not messages[cut.index - 1].tool_calls
    assert not messages[cut.index - 1].past_tool_calls
    tail_ids = {m.tool_call_id for m in messages[cut.index:] if m.role == "tool"}
    requested = {
        r.id
        for m in messages[cut.index:]
        for r in list(m.tool_calls) + list(m.past_tool_calls)
    }
    assert tail_ids <= requested


def test_consecutive_tool_calls_within_one_turn_stay_together():
    messages = [m for n in range(9) for m in _tool_turn(n, calls=3)]
    cut = choose_cut_point(messages)
    assert cut.found and cut.index is not None
    assert messages[cut.index].role == "user"
    # the three results belonging to the first tail turn are all in the tail
    assert sum(1 for m in messages[cut.index:] if m.role == "tool") % 3 == 0


def test_reopened_chat_past_tool_calls_are_respected():
    """past_tool_calls owns its results just as tool_calls does; a cut that
    ignored the second field would split a reopened conversation."""
    messages = [m for n in range(8) for m in _tool_turn(n, calls=2, past=True)]
    cut = choose_cut_point(messages)
    assert cut.found and cut.index is not None
    assert not messages[cut.index - 1].past_tool_calls


def test_conversation_shorter_than_k_turns_is_left_alone():
    messages = [m for n in range(KEEP_RECENT_TURNS) for m in _plain_turn(n)]
    cut = choose_cut_point(messages)
    assert not cut.found and cut.index is None
    assert "not long enough" in cut.reason


def test_one_giant_turn_has_no_legal_cut():
    messages = [Message(role="user", content="x" * 100)]
    messages += [
        Message(role="assistant", content="", tool_calls=[_call(i)]) for i in range(50)
    ]
    cut = choose_cut_point(messages)
    assert not cut.found and cut.index is None


def test_entirely_one_user_message():
    cut = choose_cut_point([Message(role="user", content="hello")])
    assert not cut.found and cut.index is None


def test_empty_conversation():
    cut = choose_cut_point([])
    assert not cut.found and cut.index is None


def test_cut_moves_earlier_when_the_candidate_boundary_is_illegal():
    """A history whose tool results were lost, so the turn boundary directly
    after an unanswered tool_use is not a legal cut. The chooser must move
    EARLIER (carrying more verbatim), never later, and never return the illegal
    index."""
    messages: list[Message] = []
    for n in range(6):
        messages += _plain_turn(n)
    broken_boundary = len(messages)
    # a turn whose assistant asked for tools and whose results never arrived
    messages += [
        Message(role="user", content="broken"),
        Message(role="assistant", content="", tool_calls=[_call(99)]),
    ]
    for n in range(KEEP_RECENT_TURNS):
        messages += _plain_turn(100 + n)

    cut = choose_cut_point(messages)
    assert cut.found and cut.index is not None
    illegal = turn_start_indices(messages)[-KEEP_RECENT_TURNS]
    assert not is_legal_cut(messages, illegal)
    assert cut.index != illegal
    assert cut.index < illegal
    assert cut.index <= broken_boundary
    assert cut.kept_turns > KEEP_RECENT_TURNS


def test_keep_recent_turns_must_be_at_least_one():
    with pytest.raises(ValueError):
        choose_cut_point([m for n in range(5) for m in _plain_turn(n)], keep_recent_turns=0)


def test_cut_point_is_frozen():
    cut = choose_cut_point([m for n in range(10) for m in _plain_turn(n)])
    with pytest.raises(Exception):
        cut.index = 0  # type: ignore[misc]


# --------------------------------------------------------------------------
# property-style
# --------------------------------------------------------------------------


def _random_conversation(rng: random.Random) -> list[Message]:
    messages: list[Message] = []
    for n in range(rng.randint(1, 14)):
        kind = rng.random()
        if kind < 0.45:
            messages += _plain_turn(n)
        elif kind < 0.8:
            messages += _tool_turn(n, calls=rng.randint(1, 4))
        else:
            messages += _tool_turn(n, calls=rng.randint(1, 3), past=True)
    return messages


def test_property_no_returned_cut_ever_orphans_a_tool_result_or_lands_mid_turn():
    rng = random.Random(20260812)
    cuts_found = 0
    for _ in range(400):
        messages = _random_conversation(rng)
        cut = choose_cut_point(messages)
        if not cut.found:
            continue
        cuts_found += 1
        assert cut.index is not None
        i = cut.index
        # lands on a turn boundary, with content on both sides
        assert 0 < i < len(messages)
        assert messages[i].role == "user"
        # the assistant just before the cut did not request tools
        assert not messages[i - 1].tool_calls and not messages[i - 1].past_tool_calls
        # every tool result on each side is answered by a tool_use on the SAME side
        for part in (messages[:i], messages[i:]):
            requested = {
                r.id
                for m in part
                for r in list(m.tool_calls) + list(m.past_tool_calls)
            }
            answered = {m.tool_call_id for m in part if m.role == "tool"}
            assert answered <= requested
            assert requested <= answered
        assert is_legal_cut(messages, i)
    assert cuts_found > 100  # the generator really did exercise the cutting path
