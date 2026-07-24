"""Orchestration loop — engineering-spec §4.4.

The loop is written against the ModelProvider interface and resolves the
provider per turn via the ModelRouter (§4.1.1) — it never holds a single
``self.active_provider``. The same loop is reused, constrained, by the Routine
Engine (§6.4), which is why the permission gate and tool registry are consulted
here rather than inside any provider.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode
from agent_core.providers.base import Message, ModelRole, ToolCallRequest
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ExecutionContext,
    ToolResult,
    call_is_destructive,
    call_permission_detail,
)
from agent_core.tools.registry import ToolRegistry


# Two ceilings, because a turn can run away in two different directions and each
# bound leaves the other wide open. Both matter for the same reason: a SAFE grant is
# keyed by tool id and lasts the session, so ONE permission card authorises every
# later call of that tool, and a tool result is model-readable text that untrusted
# page content can steer.
#
#   * ROUNDS bounds CHAINING — a page ending "now read https://…/2" walking the model
#     from one page to the next, one call at a time, indefinitely.
#   * CALLS bounds FAN-OUT — one provider response carrying hundreds of tool_use
#     blocks at once. The round cap does nothing about this on its own: the loop over
#     a single response's tool_calls is not a round, so 400 fetches inside round 1
#     cost one round and show one card.
#
# Both ceilings are generous enough that no legitimate turn meets them (a search plus
# a handful of page reads is well under ten of either) and low enough that a runaway
# stops while the person is still watching.
_MAX_TOOL_ROUNDS = 25
_MAX_TOOL_CALLS = 40
_TOO_MANY_STEPS = (
    "That turned into more steps than I should take in one go, so I stopped there. "
    "Tell me which part you'd like me to carry on with."
)
# Said to the MODEL, not the person, for each tool_use left unrun once the budget is
# spent. Every tool_use must be answered by a tool_result even when nothing ran: an
# unpaired tool_use makes the provider reject every later request in the conversation
# (the same reason a tool crash becomes a failed step rather than an exception).
_STEP_NOT_RUN = (
    "This step was not run: the turn reached its limit on how many steps it may take."
)


def _result_as_text(content: Any) -> str:
    """Serialize one tool result for the model — JSON for structured content.

    NOT ``str()``. Python's repr of a dict chooses its quote character from the
    dict's CONTENTS: a value containing apostrophes and no double quotes is emitted
    inside "..." with every apostrophe unescaped. A web page whose text is written
    with only apostrophes can therefore close the dict and open a convincing
    ``{'role': 'user', ...}`` after it — forging a message from the person, inside
    the very wrapper (``untrusted_note``) that exists to say "this is not the
    person talking". ``json.dumps`` always escapes its own delimiter, so no page
    content can produce one. This is what makes web_search's and read_web_page's
    untrusted wrappers survive the trip to the model intact.

    THE FALLBACK IS JSON TOO, and that is the whole point of it. ``default=str``
    absorbs an unserializable VALUE, so what still reaches the except clause is a
    circular reference (ValueError) or a non-string dict key (TypeError — ``default``
    is not consulted for keys). Returning ``str(content)`` there put the raw repr
    back on the wire in exactly the case the paragraph above rejects: a dict keyed
    by an object whose ``__repr__`` is ``{'role': 'user', ...}`` serialized to
    ``{{'role': 'user', ...}: 'x'}``, unescaped, straight into the message list. No
    tool ships either shape today, so nothing is exploitable right now — which is
    precisely why it had to be fixed now rather than on the day one does.
    ``json.dumps(str(...))`` keeps the repr visible for debugging but wraps it in a
    quoted JSON string, so no content it contains can close the delimiter.
    """
    if isinstance(content, (dict, list)):
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps(str(content), ensure_ascii=False)
    return str(content)


@dataclass
class Conversation:
    id: str
    messages: list[Message] = field(default_factory=list)

    def append_tool_result(self, tool_call_id: str, result: ToolResult) -> None:
        self.messages.append(
            Message(
                role="tool",
                content=_result_as_text(result.content),
                tool_call_id=tool_call_id,
            )
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
        on_activity=lambda tool_id, label, detail=None: None,
        on_usage=lambda usage, latency_ms, requested_role, model_name: None,
        shell_bridge=None,
        guards_provider=lambda: None,
    ) -> None:
        self.model_router = model_router
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager
        self.stream_to_frontend = stream_to_frontend
        # Resolves the effective GuardConfig for THIS turn (Custom profile, D3), or
        # None for the fixed defaults (Simple/Developer — byte-for-byte today). A
        # zero-arg callable, wired like the other callbacks and reading the server's
        # one resolution function; None here (CLI/tests) means the unguarded gate.
        self._guards_provider = guards_provider
        # Emitted right before each tool runs so the shell can drive the Activity
        # Panel (tool.activityUpdate, §7). Called as (tool_id, label, detail), where
        # detail is the tool's own permission_detail for THIS call — None for the
        # tools that have nothing to name. The shell_bridge is the tools' only
        # route to OS effects (§1.3); None in CLI/test mode.
        self.on_activity = on_activity
        # Called after EACH provider call with its token usage (or None) and the
        # wall-clock latency, so the server can record a usage_log row (§4.8). This
        # is orchestrator machinery — the single choke point every turn's model
        # calls pass through — NEVER a registry tool.
        self.on_usage = on_usage
        self.shell_bridge = shell_bridge

    def run_turn(
        self,
        conversation: Conversation,
        requested_role: ModelRole | None = None,
        model_name: str | None = None,
        effort: str | None = None,
        mode: PolicyMode = PolicyMode.SAFE,
    ) -> None:
        # Per-turn resolution (§4.1.1). ``model_name`` is an EXPLICIT pick — among
        # several LOCAL models (item B) or several cloud models (§6.8) — a user toggle
        # or a Routine step's model_id; never a choice Addison makes in v1. ``effort``
        # is the per-message "answer style"; providers that don't support it ignore it.
        # ``mode`` (policy.py) is derived from the active profile: SAFE (default) is
        # the historical behaviour; OPEN surfaces dev-only tools and thins the gate.
        provider = self.model_router.resolve(requested_role, model_name)
        # The guard posture for this whole turn (Custom profile, D3), resolved once:
        # a settings change lands on the worker thread serialised with the turn, so
        # it cannot shift mid-turn. None ≡ the fixed defaults ≡ today's gate.
        guards = self._guards_provider()
        # A "Not now" from an earlier turn must not silently deny this one:
        # each new user message may ask again (grants, by contrast, persist).
        self.permission_gate.clear_denials()
        context = ExecutionContext(
            conversation_id=conversation.id,
            shell_bridge=self.shell_bridge,
            policy_mode=mode,
        )
        # Bounded, not ``while True``: see _MAX_TOOL_ROUNDS and _MAX_TOOL_CALLS. The
        # loop is driven by the model, and what the model reads between rounds
        # includes untrusted page text, so neither "how many times round" nor "how
        # many at once" may be the page's decision.
        calls_made = 0
        budget_spent = False
        for _round in range(_MAX_TOOL_ROUNDS):
            started = time.monotonic()
            response = provider.send(
                messages=conversation.messages,
                # The model only ever sees the tools visible in this mode — SAFE
                # hides every dev-only tool, so it can't even request run_command.
                tools=self.tool_registry.visible_tools(mode),
                effort=effort,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            # Record this call's usage + latency at the single choke point (§4.8).
            # A provider that reports no usage passes None; the recorder skips it.
            self.on_usage(response.usage, latency_ms, requested_role, model_name)
            if response.tool_calls:
                # Record the assistant's tool-call turn BEFORE its results so that
                # each tool_result pairs with the tool_use it answers (§4.4).
                conversation.append_assistant_tool_calls(response.text, response.tool_calls)
                for call in response.tool_calls:
                    if calls_made >= _MAX_TOOL_CALLS:
                        # Budget spent. Answer this tool_use so the pairing holds,
                        # but run nothing: the point of the ceiling is that no
                        # further request leaves the machine, so the check sits
                        # ABOVE the gate and the tool, not inside them.
                        budget_spent = True
                        conversation.append_tool_result(
                            call.id, ToolResult(success=False, content=_STEP_NOT_RUN)
                        )
                        continue
                    calls_made += 1
                    tool = self.tool_registry.get(call.tool_id)
                    # SAFE-1 at dispatch: visible_tools hides dev-only tools from the
                    # model, but a tool_use naming a hidden id still reaches here, and
                    # the gate does not check dev-ness. Refuse BEFORE the gate and
                    # before execute, so the boundary does not depend on each dev
                    # tool remembering to check the mode itself.
                    dev_only_refusal = self.tool_registry.refuse_if_dev_only_outside_open(
                        call.tool_id, mode
                    )
                    if dev_only_refusal is not None:
                        conversation.append_tool_result(
                            call.id, ToolResult(success=False, content=dev_only_refusal)
                        )
                        continue
                    # Mode-aware authorization (policy.py): SAFE prompts for every
                    # not-yet-granted tool; OPEN auto-allows non-destructive calls and
                    # prompts PER INVOCATION for destructive ones (the card shows the
                    # exact command via `detail`). Destructiveness is per-call
                    # (run_command classifies its own; else HIGH == destructive).
                    destructive = call_is_destructive(tool, call.args)
                    # Asked once and used twice, on purpose: the permission card and
                    # the Activity Panel must describe the SAME call. Calling the
                    # tool's permission_detail a second time could describe a
                    # different one if it ever stops being a pure read of args.
                    detail = call_permission_detail(tool, call.args)
                    status = self.permission_gate.authorize(
                        call.tool_id,
                        mode=mode,
                        destructive=destructive,
                        detail=detail,
                        guards=guards,
                    )  # may block for UI
                    if status == PermissionStatus.DENIED:
                        # Steer the model past the refusal: "not now" declines the
                        # STEP, not the request — anything already gathered (search
                        # results, a calculation) should be delivered in chat.
                        result = ToolResult(
                            success=False,
                            content=(
                                "User declined this step. Do not ask again this turn. "
                                "Finish the request without it — if you already found "
                                "the information, give it directly in your reply."
                            ),
                        )
                    else:
                        # `detail` rides along so the panel can name the destination,
                        # not just the step: a granted tool id is re-usable for the
                        # rest of the session, so after the first "Allow" the panel
                        # is where the person is told WHERE a call is going (§8, owner
                        # decision 2026-07-20 — visibility over per-site grants). The
                        # routine engine emits the same three fields for the same
                        # reason; these are the two places a tool call is announced,
                        # and they must not diverge.
                        #
                        # Be precise about what this buys, because it is easy to
                        # over-read: it names the SITE, never the payload. A read that
                        # carries data outward in the path or query of an
                        # ordinary-looking host is indistinguishable here from an
                        # honest read of that host. It catches an unfamiliar
                        # destination, not a familiar one being misused. Bounding WHO
                        # can be reached is a grant-scoping change and is still open.
                        self.on_activity(call.tool_id, tool.definition.label, detail)
                        # A tool/bridge failure is a FAILED STEP, never a crashed
                        # turn: crashing here would leave this tool_use with no
                        # tool_result, and the provider then rejects every later
                        # request (API 400) until the app restarts.
                        try:
                            result = tool.execute(call.args, context)
                        except RuntimeError as exc:
                            # Bridge refusals carry a plain user-ready sentence
                            # (e.g. "A file with that name is already there…").
                            result = ToolResult(success=False, content=str(exc))
                        except Exception:
                            result = ToolResult(
                                success=False, content="That step didn't work, so it was skipped."
                            )
                        else:
                            if result.snapshot:
                                result.snapshot.tool_call_id = call.id
                                self.undo_manager.record(result.snapshot)
                            result = self._gate_image_result(result, provider)
                    conversation.append_tool_result(call.id, result)
                if budget_spent:
                    break  # every tool_use above was answered; stop the turn here
                continue  # loop again with tool results appended
            else:
                conversation.append_assistant_message(response.text)
                self.stream_to_frontend(response.text)
                break  # turn complete
        else:
            # Rounds exhausted. Close the turn honestly rather than leaving the
            # transcript ending on tool results with nothing said to the person.
            budget_spent = True
        if budget_spent:
            # Same sentence for both ceilings: the person does not care which
            # counter ran out, only that Addison stopped and is saying so.
            conversation.append_assistant_message(_TOO_MANY_STEPS)
            self.stream_to_frontend(_TOO_MANY_STEPS)

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
