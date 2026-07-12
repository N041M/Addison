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
