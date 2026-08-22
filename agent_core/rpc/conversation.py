"""conversation.* handlers — send a message, persist the transcript, and the
new/load/list history surface (engineering-spec §7, §4.8)."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from agent_core.context_budget import assess_budget, choose_cut_point
from agent_core.context_continuation import (
    CONTINUATION_NOTE,
    build_seed_messages,
    build_summary_request,
    usable_summary,
)
from agent_core.orchestrator import Conversation
from agent_core.providers.base import Message, ModelRole, ToolCallRequest
from agent_core.providers.router import LOCAL_ONLY
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import (
    _BYOK_ONBOARDING_MESSAGE,
    _KEY_UNREADABLE_MESSAGE,
    _SERVER_ERROR,
)
from agent_core.secret_presence import SecretPresence, may_reach_setup_relay
from agent_core.skills import compose_skills_prompt

# Frozen copy (D6/D8). local_only's privacy invariant OUTRANKS the explicit picker
# ([MF-C]): an explicit cloud pick under local_only is refused, never honoured — or
# the "nothing leaves this machine" promise is breakable per message. The empty-pool
# sentence speaks of models only (read_web_page is unaffected; the copy must not
# over-promise).
_LOCAL_ONLY_REFUSES_CLOUD = (
    "You've set Addison to use only models on this computer, so it didn't use {x}. "
    "Change how models are picked to use cloud models again."
)
_LOCAL_ONLY_EMPTY_POOL = (
    "You've asked Addison to use only models on this computer, and there aren't any "
    "set up yet. Add one under Local models, or change how models are picked."
)
# Frozen copy. Said when ``conversation.sendMessage`` arrives with nothing in it.
#
# THE CORE KEEPING ITS OWN INVARIANT, not a second copy of the composer's rule.
# ``Composer.tsx`` already refuses an empty send (`submit()` trims and returns, and
# the button is disabled), and the CLI skips a blank line — so no shipped caller can
# reach this today. That is exactly why it was missing, and why it belongs here: an
# empty message used to persist a blank ``user`` row, and rollback does not remove
# it, so the one caller that ever gets this wrong leaves permanent litter in
# somebody's transcript. A guard whose only proof is "nothing calls it wrongly" is
# not a guard.
_NOTHING_TO_SEND = "There's nothing to send yet — write a message first."

# The Activity Panel channel the routing and screening notes already use, with its
# own synthetic id (orchestrator._ROUTING_ACTIVITY_ID / _SCREENING_ACTIVITY_ID are
# the precedent). A continuation is a note ABOUT the conversation rather than a
# step in it, and _emit_activity's contract is tool-agnostic on purpose, so this
# needs no new IPC method and no new visual vocabulary, which is why §4.8's
# "boundary marker in the thread" is served by the marker channel that exists
# rather than by a second one invented for it.
_CONTEXT_ACTIVITY_ID = "context"


def _encode_tool_calls(message: Message, shown_steps: dict[str, str | None]) -> str | None:
    """One assistant turn's tool calls, as the JSON ``messages.tool_calls_json``
    holds (schema.sql owns why the column exists).

    ``shown_steps`` is the orchestrator's record of which calls actually ran and
    how the panel described each one, so ``ran`` and ``detail`` are observations
    rather than guesses. Returns None — never "[]" — when the message asked for
    nothing, so the column stays NULL on the rows it says nothing about.

    Anything that will not serialize is dropped rather than raising: this runs
    inside the persist loop of a turn that has already succeeded, and a routine
    the person cannot save afterwards is a far smaller loss than an answer that
    disappears with an exception on its way to disk."""
    calls = getattr(message, "tool_calls", None)
    if not calls:
        return None
    rows = []
    for call in calls:
        rows.append(
            {
                "id": call.id,
                "tool_id": call.tool_id,
                "args": call.args,
                "ran": call.id in shown_steps,
                "detail": shown_steps.get(call.id),
            }
        )
    try:
        return json.dumps(rows, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _decode_tool_calls(raw) -> list[tuple[ToolCallRequest, bool, str | None]]:
    """The inverse, defensively: (call, ran, detail) per entry.

    A row whose JSON is missing, malformed or the wrong shape yields NOTHING, and
    that is the honest answer — an unreadable record of a turn is not evidence
    that the turn did anything. Nobody writes this column but the encoder above,
    so a bad value means a hand-edited database or a future shape, neither of
    which may take a conversation down on open."""
    if not isinstance(raw, str) or not raw:
        return []
    try:
        rows = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    out: list[tuple[ToolCallRequest, bool, str | None]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("tool_id"), str):
            continue
        args = row.get("args")
        detail = row.get("detail")
        out.append(
            (
                ToolCallRequest(
                    id=str(row.get("id") or ""),
                    tool_id=row["tool_id"],
                    args=args if isinstance(args, dict) else {},
                ),
                row.get("ran") is True,
                detail if isinstance(detail, str) and detail else None,
            )
        )
    return out


def _auto_title(text: str) -> str | None:
    """Derive a conversation title from its first user message: whitespace runs
    collapsed to single spaces, trimmed to the first 60 characters (with an
    ellipsis when something was cut). None for an effectively empty message —
    the history list then falls back to "Untitled"."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) > 60:
        return collapsed[:60] + "…"
    return collapsed


class ConversationMixin(ServerContext):
    def _other_cloud_provider_connected(self) -> bool:
        """Is any NON-Anthropic cloud provider marked connected in provider_config?

        Standing evidence that the person has a PRIMARY-capable setup even with no
        Anthropic key — the §4.6 relay handoff is for having no key at all, so this
        keeps an OpenAI/Google/custom-only setup on normal routing. Metadata only
        (no key is read), and a failure just answers False: this widens nothing on
        its own, it only prevents a wrongful detour to onboarding."""
        try:
            return any(
                cfg["provider_id"] != "anthropic" and cfg["connected"]
                for cfg in self.store.list_provider_configs()
            )
        except Exception:
            return False

    def _run_send_message(self, params: dict, request_id) -> None:
        text = params.get("text", "")
        # FIRST, and before anything is read, cleared or written. An empty turn has
        # no honest outcome further down: `_ensure_conversation` would create the
        # conversation row, `_persist_message` would write a blank `user` message,
        # and neither is removed by a rollback or by the failed-turn cleanup below
        # (which only trims what the TURN appended, from `pre_turn` on).
        #
        # Ahead of the pending-pick reset too: a refusal must not silently consume a
        # `model.setRoleForNextMessage` the person made for the message they are
        # about to write. Nothing happened, so nothing is spent.
        #
        # A non-string `text` is refused by the same sentence rather than coerced —
        # `str(None)` persists the four characters "None" as somebody's message.
        if not isinstance(text, str) or not text.strip():
            self._respond_error(request_id, _SERVER_ERROR, _NOTHING_TO_SEND)
            return
        requested_role = self._role_from(params.get("role")) or self._next_role
        # §4.1.1 / §6.8: thread the explicit model pick (per-message param or the last
        # setRole) into resolve(); resolve() picks the named LOCAL/cloud model and
        # falls back gracefully if the name is unknown. ``effort`` is the per-message
        # "answer style" — validated against the chosen model, then threaded to send().
        model_name = params.get("modelId") or self._next_model_name
        effort = params.get("effort") or self._next_effort
        self._next_role = None
        self._next_model_name = None
        self._next_effort = None

        error = self._selection_error(requested_role, model_name, effort)
        if error is not None:
            self._respond_error(request_id, _SERVER_ERROR, error)
            return

        # Routing strategy governs role selection BEFORE the relay branch (D6 [MF-C]):
        # under local_only the turn is forced to the LOCAL role so the §4.6 relay
        # short-circuit below is unreachable — no model call leaves this machine, the
        # Setup Assistant relay included. An explicit cloud/PRIMARY pick is REFUSED
        # here rather than silently rerouted (the invariant outranks the picker), and
        # an empty local pool answers the plain sentence, never a cloud call.
        if self._routing_strategy() == LOCAL_ONLY:
            local_ids = set(self.model_router.available_local_models())
            explicit_cloud = requested_role is ModelRole.PRIMARY or (
                model_name is not None and model_name not in local_ids
            )
            if explicit_cloud:
                picked = self._model_label(model_name) if model_name else "cloud models"
                self._respond_error(
                    request_id, _SERVER_ERROR, _LOCAL_ONLY_REFUSES_CLOUD.format(x=picked)
                )
                return
            if not local_ids:
                self._respond_error(request_id, _SERVER_ERROR, _LOCAL_ONLY_EMPTY_POOL)
                return
            requested_role = ModelRole.LOCAL

        # Is a real PRIMARY key available right now? Both the BYOK-onboarding refusal
        # and the §4.6 Setup Assistant handoff below turn on this, so probe it ONCE
        # here rather than per branch — the probe is a keychain round-trip (§5). Only
        # a PRIMARY/default turn touches the key path; a LOCAL turn never probes.
        primary_role = requested_role in (None, ModelRole.PRIMARY)
        presence = (
            self._primary_key_status() if primary_role else SecretPresence.PRESENT
        )

        # A key Addison could not READ is not a key that isn't there, and the two
        # must not share a branch: ABSENT is onboarding, but UNKNOWN is a
        # locked keychain or a password dialog nobody answered, with the person's own
        # key sitting behind it. Sending THAT turn to the Setup Assistant relay would
        # put their message on an external service because of a dialog — so this
        # answers here, before anything is persisted and before any model is called.
        # Both profiles get the same sentence: neither onboarding path applies when
        # the question "is there a key?" has no answer yet.
        if presence is SecretPresence.UNKNOWN:
            self._respond_error(request_id, _SERVER_ERROR, _KEY_UNREADABLE_MESSAGE)
            return

        # §4.6's "no key yet" means no PRIMARY-capable provider AT ALL — not "no
        # Anthropic key". The probe above is Anthropic-only, so a person whose only
        # keys are OpenAI/Google/custom would read as keyless here, and their turn —
        # explicit model pick included — would be silently rerouted to the external
        # relay (Simple) or refused with a demand for an Anthropic key they don't
        # need (Developer). A connected provider row is standing evidence of a
        # PRIMARY-capable setup, so the turn proceeds to normal routing; if that
        # provider is genuinely unreachable right now, the send fails with its own
        # plain sentence, which is honest — unlike the relay, which is silent.
        if presence is SecretPresence.ABSENT and self._other_cloud_provider_connected():
            presence = SecretPresence.PRESENT
        # The relay is reachable on ABSENT and on nothing else — ``may_reach_setup_relay``
        # owns that rule (secret_presence.py), so this file cannot re-derive it as
        # "not present" and quietly admit UNKNOWN.
        primary_key_available = not may_reach_setup_relay(presence)

        # §4.7 onboarding by profile: the Developer profile is BYOK-first — with no
        # PRIMARY key it does NOT fall back to the Setup Assistant relay; it tells the
        # user to add their own key. Simple keeps the §4.6 relay handoff below,
        # untouched. This is an onboarding *surface* branch, not a safety branch —
        # neither path changes the gate/undo/key rules (§8.7).
        profile = self._active_profile
        if (
            primary_role
            and not primary_key_available
            and profile is not None
            and profile.onboarding == "byok_first"
        ):
            self._respond_error(request_id, _SERVER_ERROR, _BYOK_ONBOARDING_MESSAGE)
            return

        self._ensure_conversation()
        user_msg = Message(role="user", content=text)
        self.conversation.messages.append(user_msg)
        user_message_id = self._persist_message(user_msg)

        # Auto-title on the first user message. The store call is first-write-wins
        # (title IS NULL guard), so the flag is only an optimization that skips the
        # write on every later turn. ``_auto_title`` still answers None for an
        # effectively empty message and the flag still stays down when it does — the
        # guard at the top of this method means no send can reach here that way any
        # more, but ``_conversation_rows`` calls the same function on legacy rows.
        if not self._conversation_titled:
            title = _auto_title(text)
            if title is not None:
                self.store.set_conversation_title(self.conversation.id, title)
                self._conversation_titled = True

        # §4.6 handoff: a PRIMARY-bound turn with no key yet routes to the Setup
        # Assistant, with its system prompt injected FOR THIS TURN ONLY. The prompt
        # is never persisted and never enters the stored transcript (which also can't
        # hold a "system" role — messages.role CHECK is user/assistant/tool). Once a
        # key exists, the probe passes and turns go to PRIMARY, history untouched —
        # that IS the handoff; no transcript rewrite, no state to flip.
        system_msg = None
        if primary_role and not primary_key_available:
            requested_role = ModelRole.SETUP_ASSISTANT
            if self._setup_prompt:
                system_msg = Message(role="system", content=self._setup_prompt)
                self.conversation.messages.insert(0, system_msg)
        else:
            # Every non-setup turn (cloud or local) gets the app-context prompt PLUS
            # any ENABLED guidance skills (agent_core/skills.py), under the same
            # transient rules: this turn only, never persisted. A skill's text can only
            # STEER Addison — it can NEVER widen what Addison may DO (the ToolRegistry +
            # PermissionGate stay the sole authority; every tool call still hits the
            # gate). Skills are plain declarative text, so they compose in BOTH SAFE and
            # OPEN modes. With no enabled skills compose_skills_prompt returns "", so the
            # effective prompt is byte-identical to today's.
            effective_prompt = (self._primary_prompt or "") + compose_skills_prompt(
                self.store.list_enabled_skills()
            )
            if effective_prompt:
                system_msg = Message(role="system", content=effective_prompt)
                self.conversation.messages.insert(0, system_msg)

        pre_turn = len(self.conversation.messages)
        assistant_message_id: str | None = None
        # Cleared before the run so a turn that raises can never surface a previous
        # turn's answeredWith (the error path never reads it); set by _record_answered
        # (orchestrator on_answered, D5) when a turn produces a final answer.
        self._answered_with = None
        # Cleared for the same reason: the §4.8 check at the end of this method must
        # read THIS turn's measurement or none at all. A stale one from a previous
        # turn could continue a conversation on evidence about a different one.
        self._turn_context_usage = None
        try:
            self.orchestrator.run_turn(
                self.conversation,
                requested_role=requested_role,
                model_name=model_name,
                effort=effort,
                mode=self._mode(),
            )
            # Full-transcript persistence (§4.8 substrate): every message the turn
            # appended, in order, so a later rewind can target any of them by id.
            for msg in self.conversation.messages[pre_turn:]:
                persisted_id = self._persist_message(msg)
                if msg.role == "assistant":
                    assistant_message_id = persisted_id
        except Exception:
            # A failed turn must leave NO partial exchange behind: an unpaired
            # tool_use would make the provider reject every later request (API
            # 400), and unpersisted entries would break the 1:1 alignment
            # between conversation.messages and _message_ids that rewind needs.
            del self.conversation.messages[pre_turn:]
            raise
        finally:
            # Drop the transient system prompt so it never lingers in history and
            # in-memory messages stay aligned 1:1 with the persisted _message_ids.
            if system_msg is not None:
                try:
                    self.conversation.messages.remove(system_msg)
                except ValueError:
                    pass
        # Hook H8 (G3): this configuration just answered a message end to end —
        # run_turn returned normally AND every message persisted — so it is provably
        # working. Deliberately NOT in the finally above (that runs on the error path
        # too) and not at function exit (the early returns are refusals, neither
        # successes nor failures). It will happily mark a just-broken config working;
        # the correction for that lives in restore_last_working()'s fingerprint skip,
        # because a predicate that has to observe the future could not be cheap,
        # idempotent and non-raising, which this one must be.
        self._mark_verified_working()
        # THE TURN BOUNDARY (§4.8). Here and nowhere else: the turn has finished, the
        # whole exchange is on disk, and nothing is mid-flight, the only moment at
        # which a conversation may be continued without splitting a turn in half. It
        # runs after _mark_verified_working for the same reason it runs after the
        # persist loop: this turn's success is a fact about the configuration that
        # answered it, and must not depend on what the bookkeeping does next.
        self._maybe_continue_for_budget(requested_role, model_name)
        # The persisted ids let the frontend anchor "Rewind to here" on REAL
        # store ids — its own display ids mean nothing to the core.
        result = {
            "ok": True,
            "userMessageId": user_message_id,
            "assistantMessageId": assistant_message_id,
        }
        # answeredWith (D5): {modelId, label, free, routed, truncated}. The
        # transcript chip renders on ``free`` alone — known-free by construction
        # (owner decision 2026-08-12). ``truncated`` says the answer stopped at the
        # model's output cap, which is what puts "Continue this answer" beside
        # Retry (2026-08-22).
        # Absent when the turn produced no final answer (e.g. an over-budget stop).
        if self._answered_with is not None:
            result["answeredWith"] = self._answered_with
        self._respond(request_id, result)

    # --- the Context Budget Manager (§4.8) ---------------------------------
    def _maybe_continue_for_budget(self, requested_role, model_name) -> None:
        """Condense the older part of a long chat and carry on in a new one.

        §4.8's four numbered behaviours, in order: watch usage against the
        threshold, summarise the older portion through ``model_router.resolve()``,
        start a continuation conversation seeded with the summary + the confirmed
        facts + the recent turns verbatim, and tell the person one plain sentence.

        MACHINERY, NEVER A REGISTRY TOOL (hard rule 1). It is a private method on
        the server, reached only from the end of a turn. Nothing registers it, no
        model can ask for it, and it raises no permission card, the model does not
        get to decide when its own memory of a conversation is rewritten.

        NOTHING IS DELETED (hard rule 4). The original conversation's rows are not
        read for editing, not rewritten and not removed; the tail is COPIED into a
        new conversation. "What did we say earlier?" is still answerable from the
        stored transcript, which is exactly what the summary is an access path to.

        SAME UX IN BOTH PROFILES (hard rule 5). Nothing here reads the profile or
        the policy mode: Simple and Developer take this identical path and see the
        identical sentence. Developer's raw diagnostics show token counts already;
        that is a different surface reading the same numbers, not a second
        mechanism.

        EVERY FAILURE MEANS DO NOTHING, silently, leaving the conversation exactly
        as it was. That covers: a provider that cannot say how big its window is,
        no measurement at all, a chat with no legal cut point, a summary call that
        raises, and a summary that comes back empty or too short to stand in for
        anything. In none of those cases is the person told a story about a
        condensing that did not happen."""
        measurement = self._turn_context_usage
        if measurement is None:
            return  # nothing measured: cannot tell, so do nothing
        used_tokens, limit_tokens = measurement
        # (1) Watch usage against the threshold, using the RESOLVED provider's own
        # max_context_tokens for THIS turn. ``assess_budget`` owns the arithmetic and
        # the cannot-tell rule; a limit of None comes back as known=False.
        assessment = assess_budget(used_tokens, limit_tokens)
        if not assessment.known or not assessment.over_threshold:
            return
        # The relay is off limits. A turn with no key yet runs on the shared Setup
        # Assistant service (§4.6), and summarising would put the OLDER PART OF
        # SOMEBODY'S CHAT on an external service they never chose, to save tokens
        # nobody is paying for. Not a floor, a judgement, and the honest place for
        # it is here, before any transcript is assembled.
        if requested_role is ModelRole.SETUP_ASSISTANT:
            return
        # (2a) Where the older portion ends. DELEGATED ENTIRELY: hard rule 2 says cut
        # only at turn boundaries, and this file never re-derives, widens or trims
        # what choose_cut_point returned. No legal cut means do nothing.
        cut = choose_cut_point(self.conversation.messages)
        if not cut.found or cut.index is None:
            return
        older = list(self.conversation.messages[: cut.index])
        tail = list(self.conversation.messages[cut.index :])
        # (2b) The summary itself: one ``model_router.resolve()`` call with the SAME
        # role and model the turn used, so somebody who set Addison to local models
        # has this run locally too, that is the reason §4.8 specifies resolve()
        # rather than a hardcoded provider. It is a summarisation request and not a
        # tool: no registry, no gate, no card.
        try:
            provider = self.model_router.resolve(requested_role, model_name)
            response = provider.send(
                messages=build_summary_request(older),
                # No tools, ever. This call is not a turn: there is nobody to ask for
                # permission and nothing it may be allowed to do.
                tools=[],
            )
            summary = usable_summary(getattr(response, "text", None))
        except Exception:
            # Includes an unreachable provider, a rejected request, and a provider
            # that raises on send. A turn that has already answered the person must
            # not fail because bookkeeping after it did.
            return
        if summary is None:
            return  # nothing usable came back: never continue with a summary we do not have
        # (3) The continuation conversation. The confirmed facts are READ (hard rule
        # 3: memory_facts is confirmation-only, there is no write path here, and the
        # store offers no insert helper to reach for). An unreadable memory table
        # yields no facts rather than failing the continuation.
        try:
            facts = self.store.confirmed_memory_facts()
        except Exception:
            facts = []
        seed = build_seed_messages(summary, facts, tail)
        previous = self.conversation
        previous_ids = self._message_ids
        previous_titled = self._conversation_titled
        header = self.store.get_conversation(previous.id) or {}
        new_id = str(uuid4())
        try:
            self.store.create_conversation(
                id=new_id,
                title=header.get("title"),
                provider_id="primary",
                started_at=int(time.time()),
                # Lineage and summary, §4.8 item 3, the two columns the v1 substrate
                # landed and nothing has written until now.
                continued_from=previous.id,
                summary=summary,
            )
            # Switch the live conversation FIRST so _persist_message writes the seed
            # into the new conversation, and rebuild _message_ids as it goes: the 1:1
            # alignment between messages and ids is what rewind indexes by.
            self.conversation = Conversation(id=new_id, messages=seed)
            self._message_ids = []
            self._conversation_created = True
            self._conversation_titled = header.get("title") is not None
            for message in seed:
                self._persist_message(message)
        except Exception:
            # A half-made continuation is worse than none: put the person back in the
            # conversation they were in, which still has every message it had.
            self.conversation = previous
            self._message_ids = previous_ids
            self._conversation_created = True
            self._conversation_titled = previous_titled
            return
        # (4) Tell the person. One plain sentence, on the existing note channel, only
        # once everything above actually happened.
        self._emit_activity(_CONTEXT_ACTIVITY_ID, CONTINUATION_NOTE)

    def _ensure_conversation(self) -> None:
        if self._conversation_created:
            return
        self.store.create_conversation(
            id=self.conversation.id,
            title=None,
            provider_id="primary",
            started_at=int(time.time()),
        )
        self._conversation_created = True

    def _persist_message(self, message: Message) -> str:
        message_id = str(uuid4())
        self.store.insert_message(
            id=message_id,
            conversation_id=self.conversation.id,
            role=message.role,
            content=str(message.content),
            created_at=int(time.time()),
            tool_call_id=message.tool_call_id,
            # NULL for every row but an assistant turn that asked for tools.
            tool_calls_json=_encode_tool_calls(message, self.conversation.shown_steps),
        )
        self._message_ids.append(message_id)
        return message_id

    # --- conversation history (new / load / list) --------------------------
    def _handle_conversation_new(self, request_id) -> None:
        """Start a fresh conversation: new uuid, empty in-memory state. NO store
        row is inserted here — rows stay lazy via ``_ensure_conversation`` (first
        real turn), so an abandoned empty chat never appears in history."""
        self.conversation = Conversation(id=str(uuid4()))
        self._message_ids = []
        self._conversation_created = False
        self._conversation_titled = False
        self._draft_routine = None
        self._respond(request_id, {"conversationId": self.conversation.id})

    def _handle_conversation_load(self, params: dict, request_id) -> None:
        """Reopen a stored conversation as the active one.

        The in-memory state is rebuilt from the persisted transcript in one
        filtered pass that keeps user messages and non-empty assistant messages.
        Persisted ``tool`` rows (and the empty assistant stubs that requested the
        tools) are SKIPPED on purpose: replaying them would send tool_results whose
        tool_use is not in the request, and the provider then 400s on every
        subsequent turn — a resumed conversation keeps the assistant's final prose
        only. (The calls ARE stored now, in ``messages.tool_calls_json``; that is
        history, and the paragraph below is about where history is allowed to go.) Each kept row
        appends to BOTH the fresh Conversation and the fresh ``_message_ids`` list
        in the same pass; that 1:1 alignment is the rewind-anchoring invariant
        (``_handle_rewind`` indexes one list with the other's position).

        The same pass rebuilds what the SKIPPED rows are still good for
        (KNOWN-BUGS #5). Persisted tool calls do not go back into ``tool_calls`` —
        see the paragraph above, which is unchanged — they go onto
        ``Message.past_tool_calls``, which no provider reads and the routine
        builder does, so "Save as routine" works on a reopened chat. The response
        also carries ``work``: the LAST turn's steps, which is exactly what the
        live panel shows (it is cleared at the start of every turn), so reopening
        a chat redraws the panel it had instead of an accumulation of everything
        the conversation ever did."""
        self._ensure_built()
        conversation_id = params.get("conversationId")
        header = (
            self.store.get_conversation(conversation_id)
            if isinstance(conversation_id, str) and conversation_id
            else None
        )
        if header is None or not isinstance(conversation_id, str):
            self._respond_error(request_id, _SERVER_ERROR, "Couldn't find that conversation.")
            return
        conversation = Conversation(id=conversation_id)
        message_ids: list[str] = []
        wire_messages: list[dict] = []
        # Calls belonging to rows that are NOT kept in the transcript (the empty
        # assistant stub that requested the tools is the usual one). They are held
        # until the next kept assistant message and ride on that — the same turn's
        # own prose — so nothing is attributed to a message from a different turn.
        pending: list[ToolCallRequest] = []
        # Per-turn steps for the panel: a new bucket at every user message, which is
        # where a turn starts. Only calls that RAN go in; a denied step is history,
        # not work Addison did.
        turns: list[list[dict]] = [[]]
        for row in self.store.messages_for_conversation(conversation_id):
            decoded = _decode_tool_calls(row.get("tool_calls_json"))
            if row["role"] == "user":
                turns.append([])
            for call, ran, detail in decoded:
                if ran:
                    turns[-1].append(self._work_step(call, detail))
            keep = row["role"] == "user" or (row["role"] == "assistant" and row["content"])
            if not keep:
                pending.extend(call for call, _ran, _detail in decoded)
                continue
            message = Message(role=row["role"], content=row["content"])
            if row["role"] == "assistant":
                message.past_tool_calls = pending + [c for c, _r, _d in decoded]
                pending = []
            conversation.messages.append(message)
            message_ids.append(row["id"])
            wire_messages.append({"id": row["id"], "role": row["role"], "content": row["content"]})
        if pending:
            # A conversation that ends on a tool-only turn (stopped mid-answer, say)
            # has calls with no prose of their own to sit on. They stay with the
            # nearest kept assistant message rather than being dropped, because
            # dropping them is the defect: steps that exist and cannot be saved. The
            # builder's window is the conversation's recent messages either way, so
            # this changes which message carries them and not which conversation.
            for message in reversed(conversation.messages):
                if message.role == "assistant":
                    message.past_tool_calls = message.past_tool_calls + pending
                    break
        self.conversation = conversation
        self._message_ids = message_ids
        self._conversation_created = True
        self._conversation_titled = header["title"] is not None
        self._draft_routine = None
        result = {
            "conversationId": conversation_id,
            "title": header["title"],
            "messages": wire_messages,
        }
        # THE BOUNDARY, from disk. Both keys are the ones §4.8 wrote to the
        # conversations row and nothing had ever read back: the chat this one
        # carried on from, and the summary it was seeded with. They are what a
        # durable in-thread marker renders, in place of the Activity-Panel note
        # that is cleared at the start of every turn and never persisted — the
        # note is still said live, this is the same fact still being true a week
        # later. Present only on a continuation, so an ordinary chat's payload is
        # unchanged.
        if header.get("continued_from_conversation_id"):
            result["continuedFrom"] = header["continued_from_conversation_id"]
            if header.get("summary"):
                result["summary"] = header["summary"]
        # Same three fields as a live tool.activityUpdate, so the frontend renders
        # one shape through one component. Omitted entirely when the last turn did
        # no work — an absent key is what the panel already treats as "no steps".
        if turns[-1]:
            result["work"] = turns[-1]
        self._respond(request_id, result)

    def _work_step(self, call: ToolCallRequest, detail: str | None) -> dict:
        """One redrawn panel line: the same {toolId, label, detail?} an activity
        update carries.

        The label comes from the registry, which owns what a tool is CALLED, so a
        relabelled tool reads correctly in an old chat. An id nothing is registered
        under falls back to the id — ``RoutineBuilder.preview`` has always done
        exactly this for the same situation (a tool server's tool this session has
        not rediscovered), and a plain invented sentence would be a worse answer
        than the machine name the rest of the app also shows.

        The detail is the stored one and is never recomputed: a path resolved again
        today could describe the step differently from the way it was described when
        it ran, and this line is a record, not a fresh claim."""
        tool = self.tool_registry.find(call.tool_id)
        label = tool.definition.label if tool is not None else call.tool_id
        step: dict = {"toolId": call.tool_id, "label": label}
        if detail:
            step["detail"] = detail
        return step

    def _handle_rename_conversation(self, params: dict, request_id) -> None:
        """conversation.rename — the person renamed a chat (double-click its title).

        Unconditional overwrite (store.rename_conversation, unlike the NULL-guarded
        auto-title). If it's the OPEN conversation, also latch ``_conversation_titled``
        so this turn's auto-title path can't clobber the chosen name. The title is
        trimmed and length-capped; the (canonical) value is echoed back so the
        frontend adopts exactly what was stored."""
        self._ensure_built()
        conversation_id = params.get("conversationId")
        title = params.get("title")
        if not isinstance(conversation_id, str) or not conversation_id:
            self._respond(request_id, {"ok": False, "error": "Couldn't rename that chat."})
            return
        title = title.strip() if isinstance(title, str) else ""
        if not title:
            self._respond(request_id, {"ok": False, "error": "Give the chat a name."})
            return
        title = title[:120]
        if self.store.get_conversation(conversation_id) is None:
            self._respond(request_id, {"ok": False, "error": "That chat isn't here any more."})
            return
        self.store.rename_conversation(conversation_id, title)
        if conversation_id == self.conversation.id:
            self._conversation_titled = True
        self._respond(request_id, {"ok": True, "title": title})

    def _conversation_rows(self) -> list[dict]:
        """History rows for conversation.list. The title is never null: stored
        title, else the trimmed first user message (legacy rows that predate
        auto-titling), else "Untitled".

        ``continuedFrom`` is the §4.8 lineage: the id of the chat this one carried
        on from. Present only on a continuation, like every other optional key in
        this file, so an ordinary row is byte-identical to what it always was. It
        is what lets the sidebar show one continued chat as one thing; nothing is
        hidden by it, both conversations stay in the list and stay openable."""
        rows = []
        for row in self.store.list_conversations():
            title = row["title"] or _auto_title(row["first_user_message"] or "") or "Untitled"
            entry = {
                "id": row["id"],
                "title": title,
                "startedAt": row["started_at"],
                "messageCount": row["message_count"],
            }
            if row.get("continued_from_conversation_id"):
                entry["continuedFrom"] = row["continued_from_conversation_id"]
            rows.append(entry)
        return rows
