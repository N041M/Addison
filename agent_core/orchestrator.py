"""Orchestration loop — engineering-spec §4.4.

The loop is written against the ModelProvider interface and resolves the
provider per turn via the ModelRouter (§4.1.1) — it never holds a single
``self.active_provider``. The same loop is reused, constrained, by the Routine
Engine (§6.4), which is why the permission gate and tool registry are consulted
here rather than inside any provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.providers.base import Message, ModelRole
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext, ToolResult
from agent_core.tools.registry import ToolRegistry


@dataclass
class Conversation:
    id: str
    messages: list[Message] = field(default_factory=list)

    def append_tool_result(self, tool_call_id: str, result: ToolResult) -> None:
        self.messages.append(
            Message(role="tool", content=str(result.content), tool_call_id=tool_call_id)
        )

    def append_assistant_message(self, text: str | None) -> None:
        self.messages.append(Message(role="assistant", content=text or ""))


class Orchestrator:
    def __init__(
        self,
        model_router: ModelRouter,
        tool_registry: ToolRegistry,
        permission_gate: PermissionGate,
        undo_manager: UndoManager,
        stream_to_frontend=lambda text: None,
    ) -> None:
        self.model_router = model_router
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager
        self.stream_to_frontend = stream_to_frontend

    def run_turn(self, conversation: Conversation, requested_role: ModelRole | None = None) -> None:
        provider = self.model_router.resolve(requested_role)   # per-turn resolution, §4.1.1
        context = ExecutionContext(conversation_id=conversation.id)
        while True:
            response = provider.send(
                messages=conversation.messages,
                tools=self.tool_registry.list_for_model(),
            )
            if response.tool_calls:
                for call in response.tool_calls:
                    status = self.permission_gate.check(call.tool_id)
                    if status == PermissionStatus.NOT_YET_ASKED:
                        status = self.permission_gate.request(call.tool_id)  # blocks for UI
                    if status == PermissionStatus.DENIED:
                        result = ToolResult(success=False, content="User declined this permission.")
                    else:
                        tool = self.tool_registry.get(call.tool_id)
                        result = tool.execute(call.args, context)
                        if result.snapshot:
                            result.snapshot.tool_call_id = call.id
                            self.undo_manager.record(result.snapshot)
                    conversation.append_tool_result(call.id, result)
                continue  # loop again with tool results appended
            else:
                conversation.append_assistant_message(response.text)
                self.stream_to_frontend(response.text)
                break  # turn complete
