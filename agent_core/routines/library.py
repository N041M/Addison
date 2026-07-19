"""Routine library CRUD — engineering-spec §6.5.

Backs RoutineLibrary.tsx. v1 editing is limited to name, description, and
variable defaults; editing the step sequence itself is a v2 feature — for v1,
"delete and recreate via conversation" is the supported path for structural
changes (§6.5, §10).
"""

from __future__ import annotations

import time

from agent_core.routines.model import Routine, routine_from_json, routine_to_json


class RoutineLibrary:
    def __init__(self, store) -> None:
        self._store = store

    def list(self) -> list[dict]:
        """Rows for the library UI: routine + run metadata, plan decoded.
        ``createdInMode`` lets the caller hide dev-created routines in SAFE mode
        (policy.py)."""
        rows = []
        for row in self._store.list_routines():
            routine = routine_from_json(row["plan_json"])
            rows.append(
                {
                    "routine": routine,
                    "runCount": row["run_count"],
                    "lastRunAt": row["last_run_at"],
                    "createdInMode": row["created_in_mode"],
                }
            )
        return rows

    def get(self, routine_id: str) -> Routine:
        row = self._store.get_routine(routine_id)
        if row is None:
            raise KeyError("That routine doesn't exist any more.")
        return routine_from_json(row["plan_json"])

    def created_in_mode(self, routine_id: str) -> str | None:
        """The policy mode a routine was saved under ('safe' | 'open'), or None if
        it no longer exists. Drives the SAFE-mode run refusal in main.py."""
        row = self._store.get_routine(routine_id)
        return None if row is None else row["created_in_mode"]

    def update_metadata(
        self, routine_id: str, *, name=None, description=None, variable_defaults=None
    ) -> None:
        """v1: metadata + variable defaults only — NOT step edits (§6.5)."""
        routine = self.get(routine_id)
        if name is not None:
            routine.name = name
        if description is not None:
            routine.description = description
        if variable_defaults:
            for variable in routine.variables:
                if variable.name in variable_defaults:
                    variable.default = variable_defaults[variable.name]
        self._store.update_routine(
            id=routine_id,
            name=routine.name,
            description=routine.description,
            plan_json=routine_to_json(routine),
            updated_at=int(time.time()),
        )

    def record_run(self, routine_id: str) -> None:
        """Bump run_count / last_run_at after an engine run."""
        self._store.touch_routine_run_stats(routine_id, last_run_at=int(time.time()))

    def delete(self, routine_id: str) -> None:
        self._store.delete_routine(routine_id)