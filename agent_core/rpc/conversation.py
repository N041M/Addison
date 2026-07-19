"""conversation.* handlers — send a message, persist the transcript, and the
new/load/list history surface (engineering-spec §7, §4.8)."""

from __future__ import annotations

import time
from uuid import uuid4

from agent_core.orchestrator import Conversation
from agent_core.providers.base import Message, ModelRole
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _BYOK_ONBOARDING_MESSAGE, _SERVER_ERROR


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
        elif self._primary_prompt:
            # Every non-setup turn (cloud or local) gets the app-context prompt,
            # under the same transient rules: this turn only, never persisted.
            system_msg = Message(role="system", content=self._primary_prompt)
            self.conversation.messages.insert(0, system_msg)

        pre_turn = len(self.conversation.messages)
        assistant_message_id: str | None = None
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
        # The persisted ids let the frontend anchor "Rewind to here" on REAL
        # store ids — its own display ids mean nothing to the core.
        self._respond(
            request_id,
            {
                "ok": True,
                "userMessageId": user_message_id,
                "assistantMessageId": assistant_message_id,
            },
        )

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
