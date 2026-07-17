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
from agent_core.providers.base import Message, ModelRole, ToolCallRequest
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

    def append_assistant_tool_calls(
        self, text: str | None, tool_calls: list[ToolCallRequest]
    ) -> None:
        """Record the assistant turn that REQUESTED tools, before its results.

        Providers with native tool calling (e.g. Anthropic) require each
        ``tool_result`` to be preceded in history by the assistant ``tool_use``
        it answers. Without this the next ``provider.send()`` replays an
        unpaired tool result and the API rejects the turn (spec §4.4)."""
        self.messages.append(
            Message(role="assistant", content=text or "", tool_calls=tool_calls)
        )


class Orchestrator:
    def __init__(
        self,
        model_router: ModelRouter,
        tool_registry: ToolRegistry,
        permission_gate: PermissionGate,
        undo_manager: UndoManager,
        stream_to_frontend=lambda text: None,
        on_activity=lambda tool_id, label: None,
        shell_bridge=None,
    ) -> None:
        self.model_router = model_router
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager
        self.stream_to_frontend = stream_to_frontend
        # Emitted right before each tool runs so the shell can drive the Activity
        # Panel (tool.activityUpdate, §7). The shell_bridge is the tools' only
        # route to OS effects (§1.3); None in CLI/test mode.
        self.on_activity = on_activity
        self.shell_bridge = shell_bridge

    def run_turn(
        self,
        conversation: Conversation,
        requested_role: ModelRole | None = None,
        model_name: str | None = None,
        effort: str | None = None,
    ) -> None:
        # Per-turn resolution (§4.1.1). ``model_name`` is an EXPLICIT pick — among
        # several LOCAL models (item B) or several cloud models (§6.8) — a user toggle
        # or a Routine step's model_id; never a choice Addison makes in v1. ``effort``
        # is the per-message "answer style"; providers that don't support it ignore it.
        provider = self.model_router.resolve(requested_role, model_name)
        context = ExecutionContext(
            conversation_id=conversation.id, shell_bridge=self.shell_bridge
        )
        while True:
            response = provider.send(
                messages=conversation.messages,
                tools=self.tool_registry.list_for_model(),
                effort=effort,
            )
            if response.tool_calls:
                # Record the assistant's tool-call turn BEFORE its results so that
                # each tool_result pairs with the tool_use it answers (§4.4).
                conversation.append_assistant_tool_calls(response.text, response.tool_calls)
                for call in response.tool_calls:
                    status = self.permission_gate.check(call.tool_id)
                    if status == PermissionStatus.NOT_YET_ASKED:
                        status = self.permission_gate.request(call.tool_id)  # blocks for UI
                    if status == PermissionStatus.DENIED:
                        result = ToolResult(success=False, content="User declined this permission.")
                    else:
                        tool = self.tool_registry.get(call.tool_id)
                        self.on_activity(call.tool_id, tool.definition.label)
                        result = tool.execute(call.args, context)
                        if result.snapshot:
                            result.snapshot.tool_call_id = call.id
                            self.undo_manager.record(result.snapshot)
                        result = self._gate_image_result(result, provider)
                    conversation.append_tool_result(call.id, result)
                continue  # loop again with tool results appended
            else:
                conversation.append_assistant_message(response.text)
                self.stream_to_frontend(response.text)
                break  # turn complete

    def _gate_image_result(self, result: ToolResult, provider) -> ToolResult:
        """(A) Vision gate (§4.1.1 item A): don't feed a picture to a model that
        can't see it. When a tool result's content is an image (the shell reports
        ``{"kind": "image", ...}``) and the active provider reports
        ``vision=False``, replace the content with a plain-language notice and
        surface it — a WARNING plus an explicit manual switch, NEVER an automatic
        model change (that's v2). Any other result passes through untouched."""
        content = result.content
        if not (isinstance(content, dict) and content.get("kind") == "image"):
            return result
        if provider.capabilities().vision:
            return result
        notice = (
            "This file is a picture, and the model you're using can't look at "
            "pictures. Switch to a vision-capable model and try again."
        )
        self.stream_to_frontend(notice)
        return ToolResult(success=False, content=notice)
