"""undo.* handlers — undo / redo the last action and rewind a conversation
(engineering-spec §7, §6.6)."""

from __future__ import annotations

from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _SERVER_ERROR


class UndoMixin(ServerContext):
    def _undo_last_action(self) -> dict:
        results = self.undo_manager.undo_last(1)
        can_redo = self.undo_manager.can_redo()
        if not results:
            return {"ok": False, "detail": "There was nothing to undo.", "canRedo": can_redo}
        result = results[0]
        if result.success:
            return {
                "ok": True,
                "detail": f"Undid the last action ({self._label(result.tool_id)}).",
                "canRedo": can_redo,
            }
        return {
            "ok": False,
            "detail": "Couldn't undo the last action. You may need to reverse it yourself.",
            "canRedo": can_redo,
        }

    def _redo_last_action(self) -> dict:
        results = self.undo_manager.redo_last(1)
        can_redo = self.undo_manager.can_redo()
        if not results:
            return {"ok": False, "detail": "There was nothing to redo.", "canRedo": can_redo}
        result = results[0]
        if result.success:
            return {
                "ok": True,
                "detail": f"Did that again ({self._label(result.tool_id)}).",
                "canRedo": can_redo,
            }
        # The plain reason (e.g. "A file with that name is already there") beats
        # a generic sentence; redo failures carry user-ready details.
        return {
            "ok": False,
            "detail": result.detail or "Couldn't do that again.",
            "canRedo": can_redo,
        }

    def _handle_rewind(self, params: dict, request_id) -> None:
        to_message_id = params.get("toMessageId")
        if not isinstance(to_message_id, str):
            self._respond_error(
                request_id, _SERVER_ERROR, "Couldn't find that point to rewind to."
            )
            return
        try:
            # Edit-and-resend semantics: the anchor message is REMOVED too, so
            # nothing re-runs until the user actually sends again — its text goes
            # back into the composer on the frontend side.
            self.undo_manager.rewind_conversation(
                self.conversation.id, to_message_id, keep_anchor=False
            )
        except KeyError:
            self._respond_error(
                request_id, _SERVER_ERROR, "Couldn't find that point to rewind to."
            )
            return
        # Mirror the truncation in the in-memory conversation, anchor included.
        if to_message_id in self._message_ids:
            idx = self._message_ids.index(to_message_id)
            del self.conversation.messages[idx:]
            del self._message_ids[idx:]
        self._respond(request_id, {"ok": True, "detail": "Rewound the conversation."})
