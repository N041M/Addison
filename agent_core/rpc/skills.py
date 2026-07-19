"""skill.* handlers — the declarative guidance-skills surface (owner-directed
2026-07-20; agent_core/skills.py).

A skill is a named plain-TEXT note the person writes to steer HOW Addison
approaches tasks. When enabled, its text is appended to the TRANSIENT per-turn
system prompt (rpc/conversation.py). These handlers only manage that text — they
create/list/edit/toggle/delete rows. A skill is NEVER executable and can NEVER
widen what Addison may DO: every side-effecting tool call still hits the
PermissionGate exactly as before (the gate + registry stay the sole authority).
Skills therefore need no permission card of their own and apply in both SAFE and
OPEN modes (no created_in_mode).

Store-touching, so — like every other store handler (widgets/profile/stats) — these
run on the worker thread (SQLite thread affinity, see JsonRpcServer's docstring);
the dispatch table enqueues them. Params are defensively guarded (never trust the
webview's shapes), mirroring the widget handlers.
"""

from __future__ import annotations

import time
from uuid import uuid4

from agent_core.rpc.base import ServerContext
from agent_core.skills import validate_skill


class SkillsMixin(ServerContext):
    def _skill_list(self) -> dict:
        """skill.list -> every skill (id/name/instructions/enabled), oldest first."""
        self._ensure_built()
        skills = [
            {
                "id": row["id"],
                "name": row["name"],
                "instructions": row["instructions"],
                "enabled": row["enabled"],
            }
            for row in self.store.list_skills()
        ]
        return {"skills": skills}

    def _skill_create(self, params: dict) -> dict:
        """skill.create {name, instructions} -> {ok, id} | {ok:false, error}.
        New skills start enabled so a just-written note takes effect next turn."""
        self._ensure_built()
        name = params.get("name")
        instructions = params.get("instructions")
        error = validate_skill(name, instructions)
        if error is not None:
            return {"ok": False, "error": error}
        assert isinstance(name, str) and isinstance(instructions, str)  # validate_skill ensured
        skill_id = str(uuid4())
        self.store.insert_skill(
            id=skill_id,
            name=name.strip(),
            instructions=instructions.strip(),
            enabled=True,
            created_at=int(time.time()),
        )
        return {"ok": True, "id": skill_id}

    def _skill_update(self, params: dict) -> dict:
        """skill.update {id, name, instructions} -> {ok, error?}. Unknown id and
        invalid text both return a plain reason (enabled state is left untouched)."""
        self._ensure_built()
        skill_id = params.get("id")
        if not isinstance(skill_id, str) or self.store.get_skill(skill_id) is None:
            return {"ok": False, "error": "That skill isn't here any more."}
        name = params.get("name")
        instructions = params.get("instructions")
        error = validate_skill(name, instructions)
        if error is not None:
            return {"ok": False, "error": error}
        assert isinstance(name, str) and isinstance(instructions, str)  # validate_skill ensured
        self.store.update_skill(skill_id, name.strip(), instructions.strip())
        return {"ok": True}

    def _skill_set_enabled(self, params: dict) -> dict:
        """skill.setEnabled {id, enabled} -> {ok}. Toggling on/off is the whole
        point of a skill; a missing id just reports ok:false."""
        self._ensure_built()
        skill_id = params.get("id")
        if not isinstance(skill_id, str) or self.store.get_skill(skill_id) is None:
            return {"ok": False}
        self.store.set_skill_enabled(skill_id, bool(params.get("enabled")))
        return {"ok": True}

    def _skill_delete(self, params: dict) -> dict:
        """skill.delete {id} -> {ok}. Idempotent — deleting an absent skill is fine."""
        self._ensure_built()
        skill_id = params.get("id")
        if isinstance(skill_id, str) and skill_id:
            self.store.delete_skill(skill_id)
        return {"ok": True}
