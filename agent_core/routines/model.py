"""Routine data structures — engineering-spec §3, §6.2.

A Routine is a DECLARATIVE plan: an ordered / DAG-shaped sequence of calls into
the same ToolRegistry used everywhere else, with templated arguments. There is
deliberately NO free-form code / shell / eval field anywhere in this structure
(§6.1, §8.1) — do not add one without a full security review.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RoutineStep:
    step_id: str                       # local id within the routine, e.g. "step_1"
    tool_id: str                       # must reference a registered tool
    args_template: dict                # values may contain {{variable}} / {{step_id.result}} placeholders
    depends_on: list[str] = field(default_factory=list)   # step_ids that must complete first
    on_failure: str = "abort"          # "abort" | "skip" | "ask_user"
    model_role: str | None = None      # "primary" | "local" | None (None = live session toggle).
                                        # A privacy/cost-sensitive step can pin itself to "local"
                                        # regardless of the live chat's selector (§4.1.1).
    model_id: str | None = None        # optional: pin this step to a SPECIFIC named model,
                                        # overriding role-based resolution — the §6.8 Model
                                        # Cascade substrate (the module itself is v2).


@dataclass
class RoutineVariable:
    name: str
    prompt: str                        # what to ask the user for this value, if not supplied
    default: str | None = None


@dataclass
class Routine:
    id: str
    name: str
    description: str
    variables: list[RoutineVariable]
    steps: list[RoutineStep]
    # NOTE: no free-form code field exists on this structure, deliberately — §6.1.


def routine_to_json(routine: Routine) -> dict:
    """The ``routines.plan_json`` form (§6.2). Pure data both ways — the reader
    below rejects nothing silently but also never evaluates anything."""
    return {
        "id": routine.id,
        "name": routine.name,
        "description": routine.description,
        "variables": [
            {"name": v.name, "prompt": v.prompt, "default": v.default}
            for v in routine.variables
        ],
        "steps": [
            {
                "step_id": s.step_id,
                "tool_id": s.tool_id,
                "args_template": s.args_template,
                "depends_on": s.depends_on,
                "on_failure": s.on_failure,
                "model_role": s.model_role,
                "model_id": s.model_id,
            }
            for s in routine.steps
        ],
    }


def routine_from_json(data: dict) -> Routine:
    return Routine(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        variables=[
            RoutineVariable(name=v["name"], prompt=v["prompt"], default=v.get("default"))
            for v in data.get("variables", [])
        ],
        steps=[
            RoutineStep(
                step_id=s["step_id"],
                tool_id=s["tool_id"],
                args_template=s["args_template"],
                depends_on=list(s.get("depends_on", [])),
                on_failure=s.get("on_failure", "abort"),
                model_role=s.get("model_role"),
                model_id=s.get("model_id"),
            )
            for s in data.get("steps", [])
        ],
    )
