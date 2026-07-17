"""Shared prompt-based fallback tool-call parser (§4.1.2, §4.6).

Factored out of ``SetupAssistantProvider`` so ``OllamaProvider`` reuses the exact
same fenced-JSON contract for local models that lack native function-calling.
A model without native tool support is asked — via an instruction block appended
to the system prompt (``build_tool_instructions``) — to emit a single fenced JSON
block ``{"tool": ..., "args": {...}}`` when it wants a tool; ``parse_tool_call``
scans a reply for that block.

Parsing is ``json.loads`` only — NEVER ``eval`` (§8.1) — and malformed / non-tool
JSON degrades to ``None`` (plain text) rather than raising. This is the "Basic
tool support" path the design doc calibrates the user's expectations around
(design-doc §7.3.2).
"""

from __future__ import annotations

import json
import re
import uuid

from agent_core.providers.base import ToolCallRequest

# One fenced code block; the language tag (```json / ```) is optional. Captured
# lazily so the FIRST block wins, and the whole block is grabbed so nested JSON
# objects survive intact.
_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.DOTALL)


def parse_tool_call(text: str, *, id_prefix: str = "tool") -> ToolCallRequest | None:
    """Scan ``text`` for a single fenced JSON ``{"tool", "args"}`` block.

    Returns a ``ToolCallRequest`` when a well-formed block is present, else None.
    NEVER raises and NEVER evaluates code — ``json.loads`` only (§8.1). Malformed
    or non-tool JSON degrades to plain text (a None return). ``id_prefix`` names
    the synthetic call id so each caller (``setup`` / ``ollama``) stays traceable.
    """
    if not text:
        return None
    match = _FENCE_RE.search(text)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1).strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    tool_id = payload.get("tool")
    if not isinstance(tool_id, str) or not tool_id:
        return None
    args = payload.get("args", {})
    if not isinstance(args, dict):
        args = {}
    return ToolCallRequest(id=f"{id_prefix}-{uuid.uuid4().hex[:8]}", tool_id=tool_id, args=args)


def build_tool_instructions(tools: list) -> str:
    """The instruction block appended to a system prompt for a model without
    native function-calling, naming the fenced-JSON shape and the tools on offer.

    Returns "" when there are no tools (nothing to instruct). Reads only ``.id``
    and ``.description`` off each tool — providers/ must not import tools/ (the
    module-boundary rule), so tool definitions stay duck-typed here.
    """
    if not tools:
        return ""
    lines = [
        "When you need to use one of the tools below, reply with ONLY a fenced "
        "JSON block in exactly this shape and nothing else:",
        "```json",
        '{"tool": "<tool name>", "args": {"<name>": "<value>"}}',
        "```",
        "Use a tool only when it is actually needed; otherwise just reply in "
        "plain language. Never invent a tool that is not listed.",
        "",
        "Available tools:",
    ]
    for d in tools:
        lines.append(f"- {d.id}: {d.description}")
    return "\n".join(lines)
