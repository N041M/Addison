"""Tool protocol, risk tiers, and the undo contract.

Engineering-spec §3, §4.2. A tool whose risk tier is not LOW MUST implement a
real ``undo()`` — this is enforced at registration time in ``registry.py`` and
is the mechanical backbone of the whole safety model (design-doc §7.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class RiskTier(str, Enum):
    LOW = "low"        # read-only, no undo needed
    MEDIUM = "medium"  # mutating, must have undo()
    HIGH = "high"      # not permitted in v1's default registry at all


@dataclass
class ToolDefinition:
    id: str
    label: str                   # plain-language, shown in permission cards
    description: str
    risk_tier: RiskTier
    parameters_schema: dict      # JSON Schema for the tool's arguments


@dataclass
class ActionSnapshot:
    """Recorded before a mutating tool runs; consumed by ``UndoManager`` (§4.5)."""

    id: str
    tool_call_id: str
    tool_id: str
    undo_payload: dict           # tool-specific, e.g. {"created_file": "/path"}
    created_at: int
    reverted: bool = False


@dataclass
class ToolResult:
    success: bool
    content: Any                              # returned to the model as the tool_result
    snapshot: ActionSnapshot | None = None    # None for read-only tools


@dataclass
class ExecutionContext:
    """Handed to every ``Tool.execute``. Gives tools their only route to
    OS-level effects — always back through the Rust shell via IPC, never a
    raw syscall from the Agent Core (engineering-spec §1.3)."""

    conversation_id: str
    shell_bridge: Any = None     # IPC handle to the Tauri shell; None in CLI/test mode


@runtime_checkable
class Tool(Protocol):
    definition: ToolDefinition

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult: ...

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Required for any tool with risk_tier=MEDIUM or higher.

        A tool that cannot implement this MUST declare risk_tier=LOW and MUST
        NOT mutate state. Enforced at registration time — see
        ``ToolRegistry.register()``.
        """
        ...
