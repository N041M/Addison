"""undo.* handlers — undo / redo the last action and rewind a conversation
(engineering-spec §7, §6.6)."""

from __future__ import annotations

from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _SERVER_ERROR
from agent_core.snapshots.file_revert import WRITE_TOOL_ID, revert_key

# THE RESTART PROBLEM, answered honestly rather than worked around (phase-3 plan §3).
#
# The shell's write ledger is SESSION-scoped and empty on launch, while
# `action_snapshots` rows survive indefinitely — so after any restart an undo of a
# project-file edit is refused by the shell no matter how well the core remembers it.
# Today the chat UI hides that by accident (`hasUndoableActions` only flips true on a
# live activity update), and the review surface removes the accident: it reads the
# database, so it will show these edits and must not offer a button that always fails.
#
# The honest answer is to ASK FIRST — `shell.canRestoreWorkspaceFiles`, a pure query of
# that ledger with no filesystem effect — and to SAY what is true when the answer is no.
# Not to persist the ledger (its whole security property is being session-scoped) and
# not to widen the restore check to "inside a trusted root" (the shell documents the
# opposite on purpose: the ledger is session, not trust).
_CANNOT_AFTER_RESTART = (
    "Addison changed that file before the app was last restarted, so it can't put it "
    "back for you."
)


class UndoMixin(ServerContext):
    def _write_edit_lost_to_a_restart(self) -> bool:
        """Would the next undo be a project-file edit the shell can no longer put back?

        Asked BEFORE anything is attempted, so a no marks nothing and writes nothing —
        the row stays exactly as it is and the person gets a sentence instead of a
        failure. A shell that cannot be asked answers no: the attempt then runs as it
        always did and reports whatever really happens, which is better than inventing
        a restart that may not have occurred.

        Scoped to ``write_project_file``, the one ledger there is a pure query for.
        ``save_file``'s undo has a session ledger of its own (`created`) with no such
        query, so a delete-after-restart still reports today's failure — stated here
        rather than papered over, because the sentence above would be a guess about a
        mechanism this code did not ask."""
        bridge = self._shell_bridge
        if bridge is None:
            return False
        pending = self.store.recent_unreverted_snapshots(limit=1)
        if not pending or pending[0].tool_id != WRITE_TOOL_ID:
            return False
        path = revert_key(pending[0].undo_payload.get("path"))
        if path is None:
            return False
        try:
            answer = bridge.can_restore_workspace_files([path])
        except RuntimeError:
            return False
        restorable = answer.get("restorable")
        if not isinstance(restorable, dict):
            return False
        return not bool(restorable.get(path))

    def _undo_last_action(self) -> dict:
        if self._write_edit_lost_to_a_restart():
            return {
                "ok": False,
                "detail": _CANNOT_AFTER_RESTART,
                "canRedo": self.undo_manager.can_redo(),
            }
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
