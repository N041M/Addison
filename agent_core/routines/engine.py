"""Routine execution engine — engineering-spec §6.4.

CRITICAL INVARIANT (§6.4, §8.5): the engine uses the SAME ToolRegistry and
PermissionGate instances as the live orchestrator. A Routine is a shortcut for
re-issuing a sequence of tool calls, NOT a way to bypass the permission system.
It never has, and must never be given, access beyond what the user has already
granted in live conversation. No auto-escalation.

STATUS: skeleton — template resolution and topological ordering are the two
pieces to implement and unit-test first (§9).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.routines.model import Routine, RoutineStep
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext, ToolResult
from agent_core.tools.registry import ToolRegistry


@dataclass
class RoutineRunResult:
    run_id: str
    status: str                                   # 'completed' | 'failed' | 'cancelled'
    step_results: dict = field(default_factory=dict)
    detail: str = ""


def topologically_sorted(steps: list[RoutineStep]) -> list[RoutineStep]:
    """Order steps so every step's ``depends_on` come first. Raises on a cycle."""
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


class RoutineEngine:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_gate: PermissionGate,
        undo_manager: UndoManager,
    ) -> None:
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager

    def run(self, routine: Routine, variable_values: dict[str, str]) -> RoutineRunResult:
        run_id = str(uuid.uuid4())
        step_results: dict[str, ToolResult] = {}
        context = ExecutionContext(conversation_id=f"routine:{routine.id}")

        for step in topologically_sorted(routine.steps):
            resolved_args = self._resolve_template(step.args_template, variable_values, step_results)
            status = self.permission_gate.check(step.tool_id)
            if status != PermissionStatus.GRANTED:
                # Routines NEVER auto-escalate — pause and ask, exactly like §4.3.
                status = self.permission_gate.request(step.tool_id)
            if status == PermissionStatus.DENIED:
                return RoutineRunResult(run_id, "failed", step_results, "permission denied")

            tool = self.tool_registry.get(step.tool_id)
            result = tool.execute(resolved_args, context)
            if result.snapshot:
                self.undo_manager.record(result.snapshot)   # Routine runs are undoable too
            step_results[step.step_id] = result

            if not result.success:
                if step.on_failure == "abort":
                    return RoutineRunResult(run_id, "failed", step_results, str(result.content))
                elif step.on_failure == "ask_user":
                    self._pause_for_user_decision(step, run_id)
                # "skip" falls through to the next step

        return RoutineRunResult(run_id, "completed", step_results)

    def _resolve_template(self, template: dict, variables: dict, step_results: dict) -> dict:
        """Substitute {{variable}} and {{step_id.result}} placeholders. Values
        are treated strictly as data — never interpreted as code (§6.2)."""
        # TODO(step 8): implement + unit test in isolation (§9) — highest-value test here.
        raise NotImplementedError("Template resolution — spec §11 step 8.")

    def _pause_for_user_decision(self, step: RoutineStep, run_id: str) -> None:
        # TODO(step 8): emit the same UI pattern as a permission card and block.
        raise NotImplementedError("ask_user pause — spec §11 step 8.")
