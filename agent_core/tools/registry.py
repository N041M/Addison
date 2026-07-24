"""Tool registry with registration-time undo enforcement + a mode-filtered view.

Engineering-spec §4.2 and §9 (test #1). Registering a MEDIUM/HIGH-risk tool
without a genuine ``undo()`` MUST raise — this is the single most important
invariant in the codebase, so do NOT satisfy it with a no-op ``undo``.

Mode-scoped safety (owner decision 2026-07-19, policy.py): an OPEN-only tool is
NEVER present in the SAFE view of the registry. There is exactly ONE registry
instance shared by the live orchestrator and the routine engine — the SAFE/OPEN
distinction is a *filtered view* over that one registry (``visible_tools(mode)``),
never a second registry — so the no-escalation property (§8.5: routines use the
same registry + gate instances) survives unchanged.

Two independent dimensions (step 5, R3), because ``dev_only`` used to conflate
them and one new tool needs them apart:
  * ``open_only`` — VISIBILITY: absent from ``visible_tools(SAFE)`` and refused at
    dispatch outside OPEN (``refuse_if_dev_only_outside_open``). SAFE cannot see
    it, send it to the model, or run it.
  * ``allow_missing_undo`` — the EXEMPTION from the undo-at-registration check (the
    single most important invariant, spec §9). Granted ONLY to a genuinely
    irreversible OPEN-only tool (``run_command``).
``write_project_file`` is the tool that forced the split: it must be ``open_only``
(hidden from SAFE) AND undo-ENFORCED (it has a real ``undo()`` and a future edit
dropping it must fail registration). ``dev_only=True`` stays as a convenience alias
that sets BOTH — the exact shape ``run_command`` needs.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode
from agent_core.tools.base import RiskTier, Tool, ToolDefinition

# Said when a dev-only tool is reached outside OPEN mode. Plain language, and the
# same register as run_command's own refusal — the person is being told a whole
# capability belongs to another profile, not shown an enforcement detail.
DEV_ONLY_REFUSAL = "That's only available in the Developer profile."


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._open_only: set[str] = set()   # tool ids only visible/runnable in OPEN mode

    def register(
        self,
        tool: Tool,
        *,
        dev_only: bool = False,
        open_only: bool = False,
        allow_missing_undo: bool = False,
    ) -> None:
        """Register a tool along the two independent OPEN dimensions (R3).

        ``open_only`` hides the tool from the SAFE view (``visible_tools(SAFE)`` /
        ``list_for_model``) and makes ``refuse_if_dev_only_outside_open`` refuse it
        outside OPEN. ``allow_missing_undo`` is the ONLY thing that exempts a
        non-LOW tool from the undo-at-registration check. ``dev_only=True`` is the
        convenience alias that sets BOTH (``run_command``'s exact shape).

        The undo-at-registration check still raises for any non-LOW tool without a
        real ``undo()`` UNLESS ``allow_missing_undo`` — so ``write_project_file``
        (``open_only=True, allow_missing_undo=False``) is hidden from SAFE yet stays
        undo-ENFORCED, the case the split exists for."""
        open_only = open_only or dev_only
        allow_missing_undo = allow_missing_undo or dev_only
        if tool.definition.risk_tier != RiskTier.LOW and not allow_missing_undo:
            # A tool whose undo() is still the Protocol default (unimplemented)
            # or missing entirely is mechanically capped at read-only — unless it
            # is dev_only, in which case OPEN mode owns the risk explicitly.
            own_undo = getattr(type(tool), "undo", None)
            # ``not callable`` matters as much as ``is None``: an ``undo`` bound to a
            # non-callable (a string, a constant) passed this check and registered at
            # HIGH straight into the SAFE view, where the UndoManager would then fail
            # at the moment someone actually needed to reverse something. A hollow but
            # CALLABLE undo (``def undo(self): pass``) cannot be detected here — no
            # static check can — which is what the per-tool round-trip tests are for.
            if (
                own_undo is None
                or getattr(own_undo, "__isabstractmethod__", False)
                or not callable(own_undo)
            ):
                raise ValueError(
                    f"Tool '{tool.definition.id}' has risk_tier="
                    f"{tool.definition.risk_tier.value} but no undo() implementation. "
                    "Either implement undo(), set risk_tier=LOW, or register it "
                    "with allow_missing_undo (dev_only)."
                )
        if tool.definition.id in self._tools:
            raise ValueError(f"Tool '{tool.definition.id}' is already registered.")
        self._tools[tool.definition.id] = tool
        if open_only:
            self._open_only.add(tool.definition.id)

    def get(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(f"No tool registered with id '{tool_id}'.") from None

    def is_dev_only(self, tool_id: str) -> bool:
        """True for an OPEN-only tool — hidden from SAFE and refused at dispatch
        outside OPEN. Named for its original single dimension; since R3 it reports
        the ``open_only`` (visibility) set, which is what the SAFE boundary keys off
        (``run_command`` AND the step-5 file tools all belong to it)."""
        return tool_id in self._open_only

    def refuse_if_dev_only_outside_open(self, tool_id: str, mode: PolicyMode) -> str | None:
        """The SAFE-1 boundary, enforced at DISPATCH: a plain refusal sentence when
        ``tool_id`` is dev_only and ``mode`` is not OPEN, else None.

        ``visible_tools`` hides dev_only tools from the MODEL, but hiding is not
        enforcing — a tool_use naming a hidden id sails straight through to
        ``get()``, and the gate does not check dev-ness either. Until this existed
        the boundary held only because ``run_command`` refused inside its own
        ``execute``, i.e. by the diligence of one tool's author. Steps 5, 7 and 8
        add more dev-only surface; tool #2 should be safe by construction, not by
        remembering. ``run_command`` keeps its own check as belt-and-suspenders.

        Called by BOTH dispatch paths (the orchestrator turn and the routine step)
        so no execution route is left uncovered."""
        if mode is not PolicyMode.OPEN and self.is_dev_only(tool_id):
            return DEV_ONLY_REFUSAL
        return None

    def visible_tools(self, mode: PolicyMode) -> list[ToolDefinition]:
        """The tool definitions the model may call under ``mode``.

        SAFE mode hides every ``open_only`` tool — the SAFE view is byte-for-byte the
        historical registry contents. OPEN mode surfaces all of them."""
        return [
            tool.definition
            for tool_id, tool in self._tools.items()
            if mode is PolicyMode.OPEN or tool_id not in self._open_only
        ]

    def list_for_model(self) -> list[ToolDefinition]:
        """The SAFE view (dev_only tools excluded). Kept as the historical name so
        SAFE-mode callers and tests are unchanged; the orchestrator resolves the
        live view per turn via ``visible_tools(mode)``."""
        return self.visible_tools(PolicyMode.SAFE)
