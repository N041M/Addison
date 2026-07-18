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


class ShellBridge(Protocol):
    """The typed contract every OS-level effect crosses (engineering-spec §1.3).

    The Agent Core has no OS permissions of its own: filesystem, clipboard,
    external-app, and draft handoffs all go back through the Rust shell via IPC,
    which is what makes the filesystem-scope-by-picker property (design-doc §9)
    enforceable at a process boundary rather than by convention. This Protocol is
    exactly the surface the v1 tools (§4.2) need — nothing broader. The shell owns
    the risky details (a save dialog that refuses overwrites, file-format
    extraction, scoped handles); tools only call these methods.

    Not ``runtime_checkable`` on purpose: it's a structural contract for the real
    Tauri bridge and test fakes, not something we isinstance-check at runtime.
    """

    def save_new_file(self, filename: str, content: str) -> str:
        """Write ``content`` to a brand-new file the user picks; return its final
        path. The shell's save dialog REFUSES to overwrite an existing file, which
        is what keeps save_file's undo trivial (just delete what it created)."""
        ...

    def delete_file(self, path: str) -> None:
        """Delete a file this session created — the undo path for save_new_file."""
        ...

    def restore_file(self, path: str, content: str) -> None:
        """Re-create a file delete_file removed this session — the redo path.
        The shell refuses any path its own undo didn't remove."""
        ...

    def open_draft(self, to: str, subject: str, body: str) -> str:
        """Open a composed draft in the user's own mail/messaging app; return an
        opaque draft reference for a later discard. Addison never presses send."""
        ...

    def discard_draft(self, draft_ref: str) -> None:
        """Discard a draft opened by open_draft — the undo path for draft_message."""
        ...

    def read_clipboard(self) -> str:
        """Return the current clipboard text (only on an explicit paste gesture)."""
        ...

    def open_external(self, url: str) -> None:
        """Open an http(s) link in the user's default browser."""
        ...

    def read_scoped_file(self, file_handle: str) -> dict:
        """Resolve a handle from the OS file picker to its extracted content. The
        shell owns format extraction; returns ``{"content": str, "kind":
        "text"|"image"|...}``. Never accepts a raw path — scope is by picker."""
        ...


@dataclass
class ExecutionContext:
    """Handed to every ``Tool.execute``. Gives tools their only route to
    OS-level effects — always back through the Rust shell via IPC, never a
    raw syscall from the Agent Core (engineering-spec §1.3)."""

    conversation_id: str
    # IPC handle to the Tauri shell — the ShellBridge above; None in CLI/test mode.
    shell_bridge: ShellBridge | None = None


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
