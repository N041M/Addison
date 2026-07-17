"""Routine execution engine — engineering-spec §6.4.

CRITICAL INVARIANT (§6.4, §8.5): the engine uses the SAME ToolRegistry and
PermissionGate instances as the live orchestrator. A Routine is a shortcut for
re-issuing a sequence of tool calls, NOT a way to bypass the permission system.
It never has, and must never be given, access beyond what the user has already
granted in live conversation. No auto-escalation.

Template values are substituted strictly as DATA (§6.1/§6.2): a placeholder is
replaced by a string, never parsed, evaluated, or executed. There is no code
path here that interprets a resolved value.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.routines.model import Routine, RoutineStep
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext, ToolResult
from agent_core.tools.registry import ToolRegistry

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)\s*\}\}")


@dataclass
class RoutineRunResult:
    run_id: str
    status: str                                   # 'completed' | 'failed' | 'cancelled'
    step_results: dict = field(default_factory=dict)
    detail: str = ""


def topologically_sorted(steps: list[RoutineStep]) -> list[RoutineStep]:
    """Order steps so every step's ``depends_on`` come first. Raises on a cycle."""
    by_id = {s.step_id: s for s in steps}
    ordered: list[RoutineStep] = []
    visited: dict[str, int] = {}  # 0 = visiting, 1 = done

    def visit(step: RoutineStep) -> None:
        state = visited.get(step.step_id)
        if state == 1:
            return
        if state == 0:
            raise ValueError(f"Cycle detected in routine at step '{step.step_id}'.")
        visited[step.step_id] = 0
        for dep in step.depends_on:
            if dep not in by_id:
                raise ValueError(f"Step '{step.step_id}' depends on unknown step '{dep}'.")
            visit(by_id[dep])
        visited[step.step_id] = 1
        ordered.append(step)

    for step in steps:
        visit(step)
    return ordered


def resolve_template(template: dict, variables: dict, step_results: dict) -> dict:
    """Substitute ``{{variable}}`` and ``{{step_id.result}}`` placeholders.

    Resolution is pure string substitution over the template's values (including
    nested dicts/lists) — a resolved value is data handed to a tool, never code
    (§6.2). An unknown placeholder raises ValueError with a plain-language
    message so the run fails loudly instead of running a half-filled step.
    """

    def lookup(name: str) -> str:
        if name.endswith(".result"):
            step_id = name[: -len(".result")]
            if step_id in step_results:
                return str(step_results[step_id].content)
            raise ValueError(
                f"This step needs the result of '{step_id}', which hasn't run yet."
            )
        if name in variables and variables[name] is not None:
            return str(variables[name])
        raise ValueError(f"This routine needs a value for '{name}' before it can run.")

    def resolve_value(value):
        if isinstance(value, str):
            # A value that IS exactly one placeholder substitutes cleanly;
            # otherwise substitute inside the surrounding text.
            return _PLACEHOLDER.sub(lambda m: lookup(m.group(1)), value)
        if isinstance(value, dict):
            return {k: resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_value(v) for v in value]
        return value

    return {key: resolve_value(value) for key, value in template.items()}


class RoutineEngine:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_gate: PermissionGate,
        undo_manager: UndoManager,
        shell_bridge=None,
        on_ask_user: Callable[[RoutineStep, str, str], bool] | None = None,
        store=None,
    ) -> None:
        # SAME instances as the live orchestrator — never private copies (§6.4).
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager
        self.shell_bridge = shell_bridge
        # on_ask_user(step, run_id, message) -> True to continue past the failed
        # step, False to stop. Rendered by the frontend with the same card
        # pattern as a permission request (§6.2). Default: stop.
        self._on_ask_user = on_ask_user or (lambda step, run_id, message: False)
        self._store = store   # optional: writes the routine_runs log (§6.4)

    def run(self, routine: Routine, variable_values: dict[str, str]) -> RoutineRunResult:
        run_id = str(uuid.uuid4())
        step_results: dict[str, ToolResult] = {}
        step_log: list[dict] = []
        context = ExecutionContext(
            conversation_id=f"routine:{routine.id}", shell_bridge=self.shell_bridge
        )

        # Variable defaults fill anything the caller didn't supply.
        variables = {v.name: v.default for v in routine.variables}
        variables.update({k: v for k, v in (variable_values or {}).items() if v is not None})

        self._log_run_started(run_id, routine.id)

        try:
            ordered = topologically_sorted(routine.steps)
        except ValueError as exc:
            return self._finish(run_id, "failed", step_results, str(exc), step_log)

        for index, step in enumerate(ordered):
            try:
                resolved_args = resolve_template(step.args_template, variables, step_results)
            except ValueError as exc:
                return self._finish(run_id, "failed", step_results, str(exc), step_log)

            status = self.permission_gate.check(step.tool_id)
            if status != PermissionStatus.GRANTED:
                # Routines NEVER auto-escalate — pause and ask, exactly like §4.3.
                status = self.permission_gate.request(step.tool_id)
            if status == PermissionStatus.DENIED:
                step_log.append(self._log_entry(index, step, "permission denied"))
                return self._finish(
                    run_id, "failed", step_results, "You declined a permission it needs.",
                    step_log,
                )

            tool = self.tool_registry.get(step.tool_id)
            result = tool.execute(resolved_args, context)
            if result.snapshot:
                result.snapshot.tool_call_id = f"{run_id}:{step.step_id}"
                self.undo_manager.record(result.snapshot)   # Routine runs are undoable too
            step_results[step.step_id] = result
            step_log.append(
                self._log_entry(index, step, "ok" if result.success else str(result.content))
            )

            if not result.success:
                if step.on_failure == "abort":
                    return self._finish(
                        run_id, "failed", step_results, str(result.content), step_log
                    )
                if step.on_failure == "ask_user":
                    keep_going = self._on_ask_user(step, run_id, str(result.content))
                    if not keep_going:
                        return self._finish(
                            run_id, "cancelled", step_results, "Stopped at your request.",
                            step_log,
                        )
                # "skip" falls through to the next step

        return self._finish(run_id, "completed", step_results, "", step_log)

    # --- run log (§6.4: backs "show what you just did" for Routine runs) -----
    def _log_entry(self, index: int, step: RoutineStep, summary: str) -> dict:
        return {"step_index": index, "tool_id": step.tool_id, "result_summary": summary}

    def _log_run_started(self, run_id: str, routine_id: str) -> None:
        if self._store is not None:
            self._store.insert_routine_run(
                id=run_id, routine_id=routine_id, started_at=int(time.time())
            )

    def _finish(self, run_id, status, step_results, detail, step_log) -> RoutineRunResult:
        if self._store is not None:
            self._store.finish_routine_run(
                id=run_id,
                status=status,
                completed_at=int(time.time()),
                step_log=step_log,
            )
        return RoutineRunResult(run_id, status, step_results, detail)