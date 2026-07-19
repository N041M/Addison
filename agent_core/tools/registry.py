"""Tool registry with registration-time undo enforcement + a mode-filtered view.

Engineering-spec §4.2 and §9 (test #1). Registering a MEDIUM/HIGH-risk tool
without a genuine ``undo()`` MUST raise — this is the single most important
invariant in the codebase, so do NOT satisfy it with a no-op ``undo``.

Mode-scoped safety (owner decision 2026-07-19, policy.py): a ``dev_only`` tool is
allowed to skip the undo requirement (it exists ONLY for OPEN/Developer mode) and
is NEVER present in the SAFE view of the registry. There is exactly ONE registry
instance shared by the live orchestrator and the routine engine — the SAFE/OPEN
distinction is a *filtered view* over that one registry (``visible_tools(mode)``),
never a second registry — so the no-escalation property (§8.5: routines use the
same registry + gate instances) survives unchanged.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode
from agent_core.tools.base import RiskTier, Tool, ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._dev_only: set[str] = set()   # tool ids only visible in OPEN mode

    def register(self, tool: Tool, *, dev_only: bool = False) -> None:
        """Register a tool. ``dev_only`` tools exist only for OPEN/Developer mode.

        The undo-at-registration check still raises for any non-LOW tool without a
        real ``undo()`` — EXCEPT a ``dev_only`` tool, which may register at HIGH
        with no undo (``run_command`` is exactly this case). A dev_only tool is
        never surfaced by the SAFE view (``visible_tools(SAFE)`` /
        ``list_for_model``), so SAFE mode is provably unchanged by its presence."""
        if tool.definition.risk_tier != RiskTier.LOW and not dev_only:
            # A tool whose undo() is still the Protocol default (unimplemented)
            # or missing entirely is mechanically capped at read-only — unless it
            # is dev_only, in which case OPEN mode owns the risk explicitly.
            own_undo = getattr(type(tool), "undo", None)
            if own_undo is None or getattr(own_undo, "__isabstractmethod__", False):
                raise ValueError(
                    f"Tool '{tool.definition.id}' has risk_tier="
                    f"{tool.definition.risk_tier.value} but no undo() implementation. "
                    "Either implement undo(), set risk_tier=LOW, or register it dev_only."
                )
        if tool.definition.id in self._tools:
            raise ValueError(f"Tool '{tool.definition.id}' is already registered.")
        self._tools[tool.definition.id] = tool
        if dev_only:
            self._dev_only.add(tool.definition.id)

    def get(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(f"No tool registered with id '{tool_id}'.") from None

    def is_dev_only(self, tool_id: str) -> bool:
        return tool_id in self._dev_only

    def visible_tools(self, mode: PolicyMode) -> list[ToolDefinition]:
        """The tool definitions the model may call under ``mode``.

        SAFE mode hides every ``dev_only`` tool — the SAFE view is byte-for-byte the
        historical registry contents. OPEN mode surfaces all of them."""
        return [
            tool.definition
            for tool_id, tool in self._tools.items()
            if mode is PolicyMode.OPEN or tool_id not in self._dev_only
        ]

    def list_for_model(self) -> list[ToolDefinition]:
        """The SAFE view (dev_only tools excluded). Kept as the historical name so
        SAFE-mode callers and tests are unchanged; the orchestrator resolves the
        live view per turn via ``visible_tools(mode)``."""
        return self.visible_tools(PolicyMode.SAFE)
