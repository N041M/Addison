"""Context-budget decisions for long conversations (engineering-spec §4.8).

WHAT THIS IS. A context window is a per-request budget, not a per-session one:
every send replays the whole conversation, so a long chat gets slower and dearer
each turn and eventually will not fit at all. §4.8's answer is to summarise the
older part of a chat and carry the recent part over verbatim. This module is the
DECISION half of that, and only the decision half: two pure functions over data
that answer "is this chat near its limit?" and "where may it be cut?". It reads
nothing, writes nothing, calls no provider and holds no store. Everything that
acts on an answer here lives in the orchestrator.

It is isolated on purpose. Getting the cut wrong does not raise; it produces a
message history the provider refuses, or a chat that quietly loses the middle of
its own reasoning. That is the part worth testing on its own, away from I/O.

THE FOUR HARD RULES IT ENFORCES (§4.8, unchanged into v2):

  1. **Orchestrator machinery, never a registry tool.** Nothing here may be
     registered in ``ToolRegistry``, made model-invokable, or put behind a
     permission card. Registry tools are user-consented actions with risk tiers;
     this is bookkeeping, and the model does not get to decide when to rewrite
     its own memory of a conversation.
  2. **Cut only at turn boundaries.** An assistant message that requested tools
     is never separated from the tool results answering it. An unpaired
     ``tool_use`` makes the provider reject every later request in the chat (the
     pairing bug fixed in build step 4). :func:`choose_cut_point` returns a cut
     that is legal or none at all; it never returns a best effort.
  3. **The limit comes from the provider, never from a table here.** The
     threshold is a fraction of the resolved provider's
     ``ProviderCapabilities.max_context_tokens``. A provider that reports no
     usable limit yields "cannot tell", never a guess, and the caller then does
     nothing.
  4. **Nothing is deleted.** A cut point marks where a SUMMARY of the older part
     begins to stand in for it. The full transcript stays in ``messages``, and
     no function here removes, edits or reorders a message.

MESSAGE SHAPE (taken from ``providers/base.py`` and ``orchestrator.Conversation``,
not assumed). A turn opens with ``role="user"``. Inside it the assistant may take
several rounds: ``role="assistant"`` carrying ``tool_calls`` (this session) or
``past_tool_calls`` (the same turn rebuilt from the store by ``conversation.load``),
each followed by one ``role="tool"`` message per call, carrying ``tool_call_id``.
The turn closes with an assistant message that requested nothing. So a legal cut
sits exactly where a ``user`` message starts, and nowhere else.

Stdlib only. It imports nothing from ``agent_core/tools/``, ``agent_core/providers/``
or ``agent_core/routines/`` (spec §2), so the orchestrator can call it without
dragging the boundary along, in the idiom of ``screening.py`` and ``redaction.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

#: Fraction of the provider's context window at which continuation becomes worth
#: doing. §4.8 says "e.g. ~70%". It is a fraction and not a token count because
#: the windows differ by two orders of magnitude across providers, and it leaves
#: headroom for the turn that crosses it: summarising at the moment the window is
#: already full would need a request that no longer fits.
DEFAULT_THRESHOLD_FRACTION = 0.70

#: How many whole recent turns are carried over verbatim when a chat continues.
#: Four is a judgement, and the reasoning is: the summary covers everything older,
#: so this number only has to preserve what a summary is bad at, which is the
#: exact wording of what is being worked on RIGHT NOW ("that file", "the second
#: one", a pasted error). Two turns loses a question and its follow-up; large
#: numbers defeat the purpose, since the carried tail is charged in full on every
#: request of the new conversation. Four keeps a normal back-and-forth intact
#: while leaving the tail a small share of a window whose 70% we just filled.
KEEP_RECENT_TURNS = 4


@dataclass(frozen=True)
class BudgetAssessment:
    """Answer to "is this conversation near the model's limit?".

    ``known`` is False when the provider reports no usable window; then
    ``over_threshold`` is False and the caller does nothing at all. It is never a
    guess, and False-because-unknown is never to be read as False-because-fine —
    ask ``known`` first.
    """

    known: bool
    over_threshold: bool
    used_tokens: int
    limit_tokens: int | None
    threshold_tokens: int | None
    reason: str

    @property
    def cannot_tell(self) -> bool:
        return not self.known


@dataclass(frozen=True)
class CutPoint:
    """Where the older portion of a conversation ends.

    ``index`` splits a message list in two: ``messages[:index]`` is what a
    summary would stand in for, ``messages[index:]`` is carried over verbatim.
    When ``found`` is False there is no legal cut and ``index`` is None; the
    caller leaves the conversation alone. There is no third option, because a
    cut that is nearly legal is a rejected history.
    """

    found: bool
    index: int | None
    kept_turns: int
    reason: str


def assess_budget(
    used_tokens: int,
    max_context_tokens: int | None,
    threshold_fraction: float = DEFAULT_THRESHOLD_FRACTION,
) -> BudgetAssessment:
    """Compare what a turn consumed against the resolved provider's window.

    ``max_context_tokens`` comes from that provider's ``ProviderCapabilities``
    and is passed in rather than looked up here, so no per-provider number ever
    lives in this module (hard rule 3). Anything unusable — None, zero, negative
    — is "cannot tell", and so is a negative ``used_tokens``, which is nonsense
    no threshold should be derived from.
    """
    if max_context_tokens is None or max_context_tokens <= 0:
        return BudgetAssessment(
            known=False,
            over_threshold=False,
            used_tokens=used_tokens,
            limit_tokens=None,
            threshold_tokens=None,
            reason="This model does not report how much it can hold.",
        )
    if used_tokens < 0:
        return BudgetAssessment(
            known=False,
            over_threshold=False,
            used_tokens=used_tokens,
            limit_tokens=max_context_tokens,
            threshold_tokens=None,
            reason="The token count for this turn was not usable.",
        )
    if not 0 < threshold_fraction <= 1:
        raise ValueError("threshold_fraction must be greater than 0 and at most 1")

    threshold = int(max_context_tokens * threshold_fraction)
    over = used_tokens >= threshold
    return BudgetAssessment(
        known=True,
        over_threshold=over,
        used_tokens=used_tokens,
        limit_tokens=max_context_tokens,
        threshold_tokens=threshold,
        reason=(
            "This chat is close to how much the model can hold at once."
            if over
            else "This chat still fits comfortably."
        ),
    )


def _role_of(message: Any) -> str:
    return str(getattr(message, "role", "") or "")


def _requested_tools(message: Any) -> bool:
    """True if this assistant message asked for tools and therefore OWNS the tool
    results that follow it. Both fields count: ``tool_calls`` is this session's
    request, ``past_tool_calls`` is the same request rebuilt from the store when a
    chat is reopened. Reading only the first would call a reopened conversation
    safe to cut in the middle of a turn."""
    if _role_of(message) != "assistant":
        return False
    return bool(getattr(message, "tool_calls", None)) or bool(
        getattr(message, "past_tool_calls", None)
    )


def turn_start_indices(messages: Sequence[Any]) -> list[int]:
    """Indices where each turn begins, in order. A turn begins at a ``user``
    message; nothing else opens one, and ``tool`` messages are inside a turn."""
    return [i for i, m in enumerate(messages) if _role_of(m) == "user"]


def is_legal_cut(messages: Sequence[Any], index: int) -> bool:
    """Whether splitting at ``index`` yields two histories a provider accepts.

    Legal means all of: something is left on both sides, the tail opens a turn
    (so the cut is not mid-turn), and the message before the cut is not an
    assistant that requested tools whose results would land on the other side of
    it (hard rule 2). The last check is redundant while turns are shaped as
    described above, and is kept anyway: it is the condition the rule is actually
    about, and it must not depend on the shape argument staying true.
    """
    if index <= 0 or index >= len(messages):
        return False
    if _role_of(messages[index]) != "user":
        return False
    if _requested_tools(messages[index - 1]):
        return False
    return True


def choose_cut_point(
    messages: Sequence[Any],
    keep_recent_turns: int = KEEP_RECENT_TURNS,
) -> CutPoint:
    """Where the older portion ends and the verbatim tail begins.

    Keeps the last ``keep_recent_turns`` turns whole, then walks EARLIER (never
    later) until the cut is legal — moving earlier only ever carries more
    verbatim, which is always safe, while moving later would eat into a turn. If
    no legal cut exists, which is the honest answer for a chat shorter than the
    tail or a single enormous turn, ``found`` is False and the caller does
    nothing.
    """
    if keep_recent_turns < 1:
        raise ValueError("keep_recent_turns must be at least 1")

    starts = turn_start_indices(messages)
    if len(starts) <= keep_recent_turns:
        return CutPoint(
            found=False,
            index=None,
            kept_turns=len(starts),
            reason="This chat is not long enough to be worth condensing.",
        )

    candidate_position = len(starts) - keep_recent_turns
    for position in range(candidate_position, 0, -1):
        index = starts[position]
        if is_legal_cut(messages, index):
            return CutPoint(
                found=True,
                index=index,
                kept_turns=len(starts) - position,
                reason="Cut at the start of a turn, with whole turns kept after it.",
            )

    return CutPoint(
        found=False,
        index=None,
        kept_turns=len(starts),
        reason="There is no safe place to split this chat, so it was left as it is.",
    )
