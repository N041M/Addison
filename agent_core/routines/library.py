"""Routine library CRUD — engineering-spec §6.5.

Backs RoutineLibrary.tsx. v1 editing is limited to name, description, and
variable defaults; editing the step sequence itself is a v2 feature — for v1,
"delete and recreate via conversation" is the supported path for structural
changes (§6.5, §10).

STATUS: stub — wire to memory.store.Store.
"""

from __future__ import annotations

from agent_core.routines.model import Routine


class RoutineLibrary:
    def __init__(self, store) -> None:
        self._store = store

    def list(self) -> list[Routine]:
        raise NotImplementedError("List routines — spec §11 step 8.")

    def get(self, routine_id: str) -> Routine:
        raise NotImplementedError("Get routine — spec §11 step 8.")

    def update_metadata(self, routine_id: str, *, name=None, description=None, variable_defaults=None) -> None:
        """v1: metadata + variable defaults only — NOT step edits (§6.5)."""
        raise NotImplementedError("Update routine metadata — spec §11 step 8.")

    def delete(self, routine_id: str) -> None:
        raise NotImplementedError("Delete routine — spec §11 step 8.")
