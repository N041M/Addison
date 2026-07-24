"""conversation.* handlers — send a message, persist the transcript, and the
new/load/list history surface (engineering-spec §7, §4.8)."""

from __future__ import annotations

import time
from uuid import uuid4

from agent_core.orchestrator import Conversation
from agent_core.providers.base import Message, ModelRole
from agent_core.providers.router import LOCAL_ONLY
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _BYOK_ONBOARDING_MESSAGE, _SERVER_ERROR
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
    def _run_send_message(self, params: dict, request_id) -> None:
        text = params.get("text", "")
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
        primary_key_available = self._primary_key_available() if primary_role else True

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

        # Auto-title on the first user message with any content. The store call is
        # first-write-wins (title IS NULL guard), so the flag is only an
        # optimization that skips the write on every later turn; a whitespace-only
        # first message leaves the flag down so the next real one can title it.
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
        # The persisted ids let the frontend anchor "Rewind to here" on REAL
        # store ids — its own display ids mean nothing to the core.
        result = {
            "ok": True,
            "userMessageId": user_message_id,
            "assistantMessageId": assistant_message_id,
        }
        # answeredWith (D5): {modelId, label, free, routed}. The transcript chip
        # renders on ``free && routed`` — a free model the user did not choose.
        # Absent when the turn produced no final answer (e.g. an over-budget stop).
        if self._answered_with is not None:
            result["answeredWith"] = self._answered_with
        self._respond(request_id, result)

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
        tools) are SKIPPED on purpose: ``insert_message`` never persists assistant
        ``tool_calls``, so replaying persisted tool rows would send unpaired
        tool_results and the provider would 400 on every subsequent turn — a
        resumed conversation keeps the assistant's final prose only. Each kept row
        appends to BOTH the fresh Conversation and the fresh ``_message_ids`` list
        in the same pass; that 1:1 alignment is the rewind-anchoring invariant
        (``_handle_rewind`` indexes one list with the other's position)."""
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
        for row in self.store.messages_for_conversation(conversation_id):
            keep = row["role"] == "user" or (row["role"] == "assistant" and row["content"])
            if not keep:
                continue
            conversation.messages.append(Message(role=row["role"], content=row["content"]))
            message_ids.append(row["id"])
            wire_messages.append({"id": row["id"], "role": row["role"], "content": row["content"]})
        self.conversation = conversation
        self._message_ids = message_ids
        self._conversation_created = True
        self._conversation_titled = header["title"] is not None
        self._draft_routine = None
        self._respond(
            request_id,
            {
                "conversationId": conversation_id,
                "title": header["title"],
                "messages": wire_messages,
            },
        )

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
        auto-titling), else "Untitled"."""
        rows = []
        for row in self.store.list_conversations():
            title = row["title"] or _auto_title(row["first_user_message"] or "") or "Untitled"
            rows.append(
                {
                    "id": row["id"],
                    "title": title,
                    "startedAt": row["started_at"],
                    "messageCount": row["message_count"],
                }
            )
        return rows
