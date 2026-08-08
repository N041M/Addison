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

        ``createdInMode`` is DISPLAY PROVENANCE — where the routine was saved, for
        the frontend's DEV badge. It is not what decides whether a profile may use
        the routine: that question is asked of the decoded ``routine`` itself, by
        ``rpc/routines.py::_routine_needs_dev``, because answering it needs the tool
        registry as well as the plan (owner decision 2026-08-08)."""
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

    # THERE IS NO ``created_in_mode(routine_id)`` HERE ANY MORE, and its absence is
    # deliberate (owner decision 2026-08-08). It existed to answer "may this profile
    # use this routine?" off the stamp — the wrong question, wearing the right
    # answer's shape — and both its callers now ask what the routine NEEDS
    # (``rpc/routines.py::_routine_needs_dev``). The stamp still ships to the frontend
    # as a badge, through ``list()`` above; a by-id accessor exists for nothing but
    # deciding something, so leaving one here would be leaving the mistake loaded.

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