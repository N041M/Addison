# The Context Budget Manager

**Status: BUILT 2026-08-14 (PRs #120 and #122), in the form this document
describes and with the limits it states. Extended 2026-08-22: the boundary a
person sees is durable now, and the history list reads the lineage — two of the
three limits below are closed, and the third is unchanged.** Engineering-spec §4.8 is the design and
still owns the five hard rules; this file owns what actually shipped, what it does
when things go wrong, and where it falls short of §4.8's own description. Anything
else in the tree that mentions long-conversation continuation links here.

It is not a floor, not a guard and not an invariant, so
[SAFETY.md](SAFETY.md) says nothing about it. The one safety-shaped sentence worth
repeating is the one §4.8 already made: this is orchestrator machinery, never a
registry tool, so no model can ask for it and no permission card exists for it.

## 1. What is built

**The decision module: `agent_core/context_budget.py`.** Stdlib only, no I/O, no
imports from `tools/`, `providers/` or `routines/`. Two pure functions over data:
`assess_budget()` compares a turn's token count against a fraction (0.70) of the
resolved provider's `max_context_tokens`, and `choose_cut_point()` says where the
older part of a chat may end. A provider that reports no window yields "cannot
tell", never a guess. A cut is legal or it does not exist: `choose_cut_point`
keeps the last four turns whole and then walks EARLIER until the split lands on
the start of a turn, so an assistant `tool_use` is never separated from the
`tool_result` answering it.

**The building module: `agent_core/context_continuation.py`.** Also pure. It owns
the wording of the summarisation request, the bounds on what goes into it and what
comes back, the one plain sentence the person hears, and the shape of the seeded
history (the summary, then the confirmed `memory_facts`, then the recent turns
copied verbatim). A summary shorter than 20 characters is not a summary and comes
back as None.

**The measurement, at the orchestrator's existing choke point.**
`Orchestrator._report_context_usage` runs beside `on_usage` after every provider
call and hands `(used_tokens, max_context_tokens)` to a new `on_context_usage`
callback. The window is read from the RESOLVED provider's own
`ProviderCapabilities`, so a turn that fell forward onto a fallback model is
judged by the window of the model that actually answered. `main.py`'s
`_record_context_usage` keeps the LARGEST measurement of the turn, because a turn
that called tools makes several requests and the biggest one is the honest reading
of how full the chat is.

**The turn boundary: `_maybe_continue_for_budget` in `agent_core/rpc/conversation.py`.**
One private method, called at the end of a turn and nowhere else, after the
transcript is persisted and after the verified-working mark. It reads this turn's
measurement (cleared at the start of every turn so a stale one can never continue
the wrong conversation), asks `assess_budget`, asks `choose_cut_point`, makes one
summarisation call through `model_router.resolve()` with the same role and model
the turn used, creates the continuation conversation, and says one sentence.

**The summary call** carries no tools, and it sends a single fresh `user` message
rather than the chat's own history, so the model summarises some text instead of
answering the last question again. It goes through `resolve()` on purpose: somebody
who set Addison to local models has this run locally too.

**The continuation, with lineage.** A new conversation row is written with
`continued_from_conversation_id` pointing at the one it came from and `summary`
holding the condensed older history. These are the two columns step 6 landed as
substrate; until now nothing wrote them. The live conversation is switched to the
new id and the seed is persisted into it. If any of that raises, the person is put
back in the conversation they were in, which still has every message it had.

**Nothing is deleted, and that is structural rather than promised.** The original
conversation's rows are not read for editing, not rewritten and not removed. The
tail is COPIED (`dataclasses.replace`) into the new conversation, so the seeded
history and the original never share an object. The summary is an access path to a
transcript that is still there.

**The note.** One plain sentence on the Activity Panel channel the routing and
screening notes already use, with its own synthetic id `context`. It says what
happened and that nothing was deleted. It is not a modal, not a confirmation and
has nothing to decide.

**The marker in the thread, and the chat that is one chat (2026-08-22).** The note
tells a person as it happens and is gone by the next turn; these two say the same
thing for as long as the chat exists, and they say it by RENDERING WHAT WAS
ALREADY ON DISK. Nothing new is stored and nothing new is computed.

- `conversation.load` sends `continuedFrom` and `summary` off the `conversations`
  row, for a continuation only, and `ChatThread` draws a marker above the first
  message: one 11px label, one plain sentence in the note's own voice (this chat
  was getting long, Addison condensed the earlier part and carried on, nothing was
  deleted and the earlier chat is still in your history), and the summary behind a
  disclosure. It uses the 2px-rule annotation idiom the free-model line already
  uses and no accent at all: it is a fact about the chat, not an action and not a
  live state. A continuation whose summary did not survive still shows the marker,
  with nothing behind the disclosure — the boundary is the claim, the summary is
  the extra.
- `conversation.list` rows carry `continuedFrom`, and `Sidebar.lineageEntries`
  folds a chain into ONE entry: the newest part keeps its row, each older part sits
  under it indented with the mono fact `earlier` in place of a start time, and the
  group hint counts the chat once. Every conversation is still drawn and still
  opens — the grouping is a way of reading the list, never a filter on it, because
  the original transcript's reachability is what the summary is an access path to.

Neither surface reads the profile: Simple and Developer see the identical marker
and the identical list, which is hard rule 5 holding where a person can see it.

**The over-window sentence: `agent_core/providers/base.py`.** §4.8 promised since
it was written that a conversation outgrowing the window "surfaces a plain-language
error suggesting a new chat". **No code ever produced that sentence.** PR #122
built it: `exception_for_http_status` reads the provider's OWN explanation, and
when that explanation names a context length or a token count it replaces the
generic rejected-request line with one sentence saying the chat got too long for
this model, to start a new one, and that this one stays saved. The provider's words
decide, never the status code alone: most 400s are not this, and telling somebody
with a malformed request to start a new chat sends them off to repeat the failure.
The automatic layer condenses most chats before they reach this point, but two
cases it cannot reach BY DESIGN (a provider that reports no window, and a summary
call that failed) are exactly why the sentence still has to exist.

## 2. Every failure path does nothing, silently

There is one rule and it has no exceptions: if any part of this cannot be done
properly, the conversation is left exactly as it was and the person is told
nothing. A note about a condensing that did not happen would be a lie about what
Addison did.

- No measurement at all for this turn: do nothing.
- A provider that reports no window (`max_context_tokens` is None), or a provider
  whose `capabilities()` raises: cannot tell, so do nothing.
- Under the threshold: do nothing.
- No legal cut point, which is the honest answer for a short chat or one enormous
  turn: do nothing.
- The summarisation call raises, or the provider is unreachable: do nothing. A turn
  that already answered the person must not fail because bookkeeping after it did.
- The summary comes back empty, whitespace or too short to stand in for a
  conversation: do nothing.
- The memory table cannot be read: the seed carries no facts rather than failing
  the continuation.
- The continuation write raises: roll back to the previous conversation and say
  nothing.

## 3. The judgement: the Setup Assistant relay is refused outright

A turn with no key of the person's own runs on the shared Setup Assistant relay
(§4.6). When the resolved role is `SETUP_ASSISTANT`, `_maybe_continue_for_budget`
returns before any transcript is assembled, and the chat is left alone.

The reason is that summarising would put the older part of somebody's conversation
on an external shared service they never chose, in order to save tokens nobody is
paying for. The trade this feature makes (send some old messages once, so the next
requests are smaller) is a good trade against a provider the person connected
themselves and a bad one against a relay they were merely lent. This is a judgement
and not a floor: no invariant forbids it, and the honest place for the check is at
the top of the method, before anything is built.

## 4. Honest limits

**One is left.** It is real, it is not softened anywhere, and it is tracked in
[KNOWN-GAPS.md](KNOWN-GAPS.md). The other two were closed on 2026-08-22 and are
recorded below them, because a limit that was stated plainly should be withdrawn
just as plainly.

**1. The summary call's tokens are NOT written to `usage_log`, so they are
invisible in cost views.** The call is made at the RPC layer, where there is no
resolved provider id or model id to attribute a row to, and `usage_log` rows are
written by the orchestrator's `on_usage` at its choke point with both identities in
hand. A row attributed to the wrong model is worse than a missing row: it silently
corrupts `tokens_month` and the per-provider latency stat, which are the numbers
somebody uses to decide what to run. So a continuation costs one model call that no
cost view shows. The call is bounded (60,000 characters of input at most) and
happens at most once per turn, so this understates cost by a bounded amount rather
than an unbounded one, but it does understate it.

### Closed 2026-08-22

Both were the same shape of gap — a durable fact on the `conversations` row with
nothing reading it — and both were closed by reading it. Section 1 describes what
now renders; this is the record of what was wrong and what the fix does not claim.

**~~The boundary marker was EPHEMERAL, so §4.8 item 4 was only partly served.~~**
The sentence goes out on the Activity Panel note channel, which `useTurn` clears at
the start of every turn (`setActivities([])`) and nothing persists — so the note
was seen once and a chat reopened later showed no sign that a boundary was there,
while §4.8 asked for "a visible boundary marker in the thread" and a thread marker
is durable by nature. The thread now renders `continuedFrom` + `summary` from the
stored row. The note still exists and is still ephemeral, deliberately: it is the
live telling, and the marker is the standing record. What this does NOT do is make
the marker appear mid-session in the chat that was just continued — the frontend
still holds the id of the conversation it was in, and the marker is drawn when the
continuation is OPENED. The note is what covers that moment, which is what it was
for.

**~~A continued chat was two rows in the history sidebar.~~** Continuing switches
the live conversation, which is what keeps the original transcript untouched and
what makes the lineage column mean anything; the visible consequence was two rows
sharing a title with nothing saying one came from the other. `lineageEntries` in
`Sidebar.tsx` now folds a chain into one entry. It is a way of READING the list and
never a filter on it: every conversation is still drawn, still renamable and still
one click from opening, and a lineage that pointed at a conversation not in the
list — or in a circle, which the core cannot produce — leaves the rows exactly
where they were rather than dropping one.

## 5. What is deliberately NOT built

- **No user-facing setting.** No threshold to tune, no way to turn it off, no
  Custom guard. It is not a prompting guard and it will not become one.
- **No retrieval.** §4.8 named summarising, external storage with selective
  retrieval, and truncation as the only three mechanisms. This builds the first.
  Retrieval over the stored transcript is a separate question and
  [KNOWN-GAPS.md](KNOWN-GAPS.md) owns it, alongside the knowledge-base entry.
- **No writing to `memory_facts`.** Facts are read into the seed and never written,
  amended or invented. There is no insert helper beside `confirmed_memory_facts()`
  to reach for, on purpose: `memory_facts` stays confirmation-only (design-doc
  §7.6).
- **No second mechanism for Developer.** Nothing in the path reads the profile or
  the policy mode. Simple and Developer take the identical path and see the
  identical sentence; Developer's raw diagnostics show token counts already, which
  is a different surface reading the same numbers.
