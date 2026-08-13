"""Seeding a continuation conversation (engineering-spec §4.8, the acting half).

WHAT THIS IS. ``context_budget.py`` (PR 1) answers two questions and touches
nothing: "is this chat near the model's limit?" and "where may it be cut?". This
module is the second pure piece: given an answer to those, it builds the STRINGS
and MESSAGES a continuation is made of. It still reads nothing, writes nothing
and calls no provider. The one place that does I/O is
``rpc/conversation.py::_maybe_continue_for_budget``, which is the turn boundary.

Split this way for the same reason PR 1 was: the wording of a summarisation
request and the exact shape of a seeded history are the parts worth reading and
testing on their own, and they are the parts a mistake is invisible in.

THE HARD RULES THAT LAND HERE (§4.8, and each is enforced somewhere in this file
or named where it is enforced instead):

  1. **Machinery, never a registry tool.** Nothing here is registered, described
     to a model as callable, or put behind a permission card. The summarisation
     request below is a request Addison makes ABOUT a conversation, not an action
     the model may choose to take. ``tests/test_context_continuation.py`` asserts
     at source level that no registry ever learns about any of it.
  2. **Cut only at turn boundaries.** Not decided here at all:
     ``choose_cut_point`` owns it, this module is handed the tail it chose and
     never re-derives, widens or trims it.
  3. **memory_facts stays confirmation-only.** Facts are READ into the seed and
     are never written, amended or invented. There is no write path in this file
     and none in its caller.
  4. **Nothing is deleted.** The seed is built by COPYING; the original messages
     are never mutated, reordered or dropped, and the original transcript rows
     are never touched by any of this.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from agent_core.providers.base import Message

#: What Addison asks a model to do with the older part of a chat. Plain, bounded,
#: and deliberately not chatty: the answer is pasted into somebody's next
#: conversation, so it asks for the content of the chat and nothing else, no
#: preamble ("Here is a summary..."), no advice, no invented conclusions.
#:
#: It says "written down elsewhere" rather than "deleted" because nothing IS
#: deleted (rule 4), and a model told the text is gone writes a more anxious,
#: more speculative summary than one told the text is safe.
SUMMARY_INSTRUCTION = (
    "Below is the earlier part of a conversation. Write a compact summary of it "
    "so the conversation can continue without replaying every message.\n\n"
    "Keep: what the person asked for, decisions made, facts they gave, names, "
    "files and numbers that came up, and anything still unfinished.\n"
    "Leave out: pleasantries, and anything you are not sure of.\n"
    "Write plain prose. Do not add a preamble, a title, or advice. Do not invent "
    "anything that is not in the text. The full text is kept, so nothing is lost "
    "if you leave something out.\n\n"
    "----- earlier conversation -----\n"
)

#: Longest transcript handed to the summariser, in characters. The request has to
#: FIT the same window that just filled up, so the oldest part is dropped rather
#: than the newest: what is nearest the cut is what the tail refers to. Characters
#: and not tokens on purpose, a token count here would need a per-provider
#: tokenizer, which is exactly the per-provider number §4.8 forbids this layer to
#: hold. It is a coarse guard against an oversized request, not an accounting.
MAX_SUMMARY_INPUT_CHARS = 60_000

#: A summary longer than this is not a summary. Truncation is at a character
#: boundary and marked, so nothing pretends to be complete that is not.
MAX_SUMMARY_CHARS = 8_000

#: Below this, whatever came back is not usable as a stand-in for a chat ("Sure!",
#: "", a stray newline). §4.8's rule is that an unusable summary means DO NOTHING,
#: so this is the line between "we have one" and "the conversation is left alone".
MIN_SUMMARY_CHARS = 20

#: Said to the person, once, when a chat has been continued. One plain sentence
#: (§4.8 item 4, design-doc §9 "no hidden decisions"): what happened, and the
#: reassurance that matters most: nothing was deleted. No token counts, no model
#: name, no jargon, and nothing to decide: this is not a confirmation.
CONTINUATION_NOTE = (
    "This chat was getting long, so Addison condensed the earlier part into a "
    "summary and carried on. Nothing was deleted: the whole conversation is "
    "still saved."
)

#: How the summary and the confirmed facts are introduced in the seeded history.
#: They go in as ONE ``user`` message, because that is the only role a stored
#: transcript may open with (``messages.role`` is user/assistant/tool) and because
#: it is honest about where the text comes from: Addison's own bookkeeping, handed
#: to the model as context, not words the model said.
_SEED_SUMMARY_HEADING = "Earlier in this conversation (a summary Addison wrote):"
_SEED_FACTS_HEADING = "Things you have confirmed you want remembered:"
_SEED_CLOSING = (
    "The messages after this one are the most recent part of the conversation, "
    "word for word."
)


def _as_text(message: object) -> str:
    role = str(getattr(message, "role", "") or "")
    content = getattr(message, "content", "")
    return f"{role}: {content}"


def transcript_for_summary(
    messages: Sequence[object], max_chars: int = MAX_SUMMARY_INPUT_CHARS
) -> str:
    """The older portion rendered as plain text for the summariser.

    Oldest-first, one ``role: content`` line per message, and when it will not fit
    the OLDEST lines go rather than the newest (see MAX_SUMMARY_INPUT_CHARS). What
    is dropped is said out loud in the text rather than silently cut, so the model
    does not read the first surviving line as the beginning of the chat."""
    rendered = "\n".join(_as_text(m) for m in messages)
    if len(rendered) <= max_chars:
        return rendered
    return "(the beginning of this conversation is not shown here)\n" + rendered[-max_chars:]


def build_summary_request(messages: Sequence[object]) -> list[Message]:
    """The one-message history for the summarisation call.

    A single ``user`` message: this is a fresh request about some text, not a
    continuation of the chat being summarised, and sending the chat's own history
    as history would invite the model to answer the last question in it again.
    """
    return [Message(role="user", content=SUMMARY_INSTRUCTION + transcript_for_summary(messages))]


def usable_summary(text: str | None) -> str | None:
    """The summary a call came back with, or None when there isn't one.

    None is the answer for empty text, whitespace, and anything too short to
    stand in for a conversation. §4.8's failure rule is absolute and lives with
    the caller: no usable summary means the conversation is left exactly as it
    was, and Addison says nothing. A boundary marker over a summary that was
    never written would be a lie about what happened."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if len(cleaned) < MIN_SUMMARY_CHARS:
        return None
    if len(cleaned) > MAX_SUMMARY_CHARS:
        cleaned = cleaned[:MAX_SUMMARY_CHARS].rstrip() + "…"
    return cleaned


def build_seed_messages(
    summary: str, facts: Sequence[str], tail: Sequence[Message]
) -> list[Message]:
    """What the continuation conversation starts with.

    Exactly §4.8's three ingredients, in order: the summary, the user-confirmed
    ``memory_facts``, and the recent turns VERBATIM, the tail
    ``choose_cut_point`` chose, neither re-derived nor edited here (rule 2).

    Every tail message is COPIED (``dataclasses.replace``), so the seeded history
    and the original conversation never share an object: nothing this function
    returns can later mutate the transcript it came from (rule 4). The copy keeps
    ``tool_calls`` as well as role and content, which is what keeps each assistant
    ``tool_use`` paired with the ``tool_result`` answering it, an unpaired one
    would make the provider reject every request of the new conversation.

    Facts go in as plain lines, read-only. Nothing here writes a fact, and the
    seed is not a fact: it is conversation-scoped state (rule 3)."""
    parts = [_SEED_SUMMARY_HEADING, summary]
    kept_facts = [f.strip() for f in facts if isinstance(f, str) and f.strip()]
    if kept_facts:
        parts += ["", _SEED_FACTS_HEADING] + [f"- {f}" for f in kept_facts]
    parts += ["", _SEED_CLOSING]
    seed = Message(role="user", content="\n".join(parts))
    return [seed] + [replace(m) for m in tail]
