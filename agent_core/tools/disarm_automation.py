"""disarm_automation — switching one back OFF (step 8, phase 3).

================================ SAFETY FRAME ================================
**This is a TIGHTENING, and every decision below follows from that one word.**
Arming hands a command to the operating system to run on a clock, unattended and
unconfined; disarming takes it back. The two are not symmetric, and treating them
as symmetric is the mistake this file exists to avoid.

**NO KEYWORD CODE.** ``arm_automation`` raises the nonce card (plan §3); this one
raises an ORDINARY destructive card in OPEN and nothing more. A ceremony in front
of switching something OFF would mean a person who mistypes a code three times is
left with a job still running on their computer — trapped by the guard, in the
direction where every failure is safe. The same rule ``automation.remove`` and
``mcp.remove`` follow: a tightening must never be the thing a guard blocks.

**NO ``undo()``, and this is the interesting one.** ``undo`` is replayed by the
UndoManager with no gate, no card and no person: an undo of *disarm* would be an
**arm** performed with none of the ceremony arming requires — the exact hole
§5.10 and §3 exist to keep shut. So there is no ``undo`` method here at all, and
re-arming is always a fresh ``arm_automation`` with a fresh code.

**Which makes the registration the load-bearing detail.** It registers
``dev_only=True`` — ``open_only`` PLUS ``allow_missing_undo``, ``run_command``'s
exact shape (registry.py R3) — and NOT as LOW.

  *Why not LOW-with-no-undo, which would also register?* Because LOW means
  "read-only, no undo needed" (``RiskTier``'s own words), and this removes a job
  from the operating system. Declaring a mutating tool LOW to slip past the
  undo check is the no-op ``undo`` in different clothing, and SAFE invariant 2's
  text is explicit that a tool which cannot be undone "stays LOW **and
  read-only**". This one is not read-only, so it stays HIGH and takes the waiver
  the waiver exists for: a genuinely un-undoable OPEN-only tool that is never in
  the SAFE view.

  The invariant is not weakened by that, because the invariant's subject is the
  SAFE surface: ``visible_tools(SAFE)`` cannot see this tool, dispatch refuses it
  outside OPEN, and inside OPEN every call meets a card. What the waiver buys is
  the freedom to be HONEST about the tier instead of lying about it.

**NO ``command_text``, DELIBERATELY.** ``arm_automation`` declares one so the
hardline denylist re-checks the row's command before arming — a row written before
the fence grew must not be armable now. Declaring it HERE would do the opposite: a
row whose command the denylist has since learned to refuse would become a job that
**cannot be switched off through Addison**, because the refusal lands above the
gate at every dispatch site. The dangerous direction and the safe direction share
one predicate, so only the dangerous one gets to ask it.

Two things this shares with its sibling unchanged: it is refused from a routine
step and a widget (``live_only``, plan §5.10 — a stored spec answering a card on
autopilot is what the ceremony exists to break, and that reasoning does not stop
applying just because this direction is safe), and the shell does the work
(``Method.SHELL_DISARM_AUTOMATION`` takes a LABEL, never a document, and is
idempotent: a label that is not installed is already in the state this asks for).
=============================================================================
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_core.automations import Automation
from agent_core.policy import PolicyMode
from agent_core.redaction import redact
from agent_core.tools.arm_automation import (
    AutomationLookup,
    arming_is_supported,
    resolve_automation,
)
from agent_core.tools.base import (
    ExecutionContext,
    RiskTier,
    ToolDefinition,
    ToolResult,
)

if TYPE_CHECKING:
    from agent_core.memory.store import Store


# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------

_NOT_READY = "Addison can't turn an automation off just yet. Try again in a moment."
_ONLY_IN_DEVELOPER = "Turning an automation off is only available in the Developer profile."
_ONLY_ON_A_MAC = (
    "Addison only switches automations on and off for your computer on a Mac, so "
    "there was nothing to switch off here."
)
# Its sibling's correction, in this direction (see ``arm_automation``'s note): the
# old sentence announced a deletion Addison had no way of knowing about, and said it
# to anybody who gave a name instead of an id.
_NO_SUCH_AUTOMATION = (
    "Addison couldn't find a saved automation with that name or id, so nothing was "
    "switched off. Check the Automations list in Settings for the exact name."
)
_AMBIGUOUS_NAME = (
    "More than one saved automation has that name, so Addison didn't switch anything "
    "off. Open the Automations list in Settings and say which id you mean."
)
_NO_SHELL = (
    "Addison can only switch an automation off from the desktop app, so it didn't "
    "switch that one off."
)
_COULDNT_DISARM = (
    "Your computer wouldn't switch that automation off just now. Try again in a "
    "moment."
)


def _disarmed_text(row: Automation) -> str:
    """What the model relays. Two facts: it is off, and the automation is still
    written down — because "switched off" and "deleted" are different things and a
    person who thinks they deleted it will be surprised to find it on the list."""
    return (
        f'"{row.name}" is switched off. Your computer won\'t run it any more. It is '
        "still saved, so it can be switched back on."
    )


class DisarmAutomationTool:
    definition = ToolDefinition(
        id="disarm_automation",
        label="Switch an automation off",
        description=(
            "Switches off an automation this computer was running by itself, so it "
            "stops running. The automation stays saved and can be switched back on "
            "later. Mac only, and available only in the Developer profile."
        ),
        risk_tier=RiskTier.HIGH,
        parameters_schema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "Which saved automation to switch off — the id from the list "
                        "of automations, or its exact name if that is all you have. "
                        "If two automations share a name, only the id will do."
                    ),
                }
            },
            "required": ["id"],
        },
    )

    def __init__(self, store_ref: Callable[[], Store | None]) -> None:
        """``store_ref`` is late-bound, as everywhere in this subsystem.

        No ``shell_bridge`` parameter, and its absence is the contract: the bridge
        is injected at construction ONLY for an ``undo()`` that has no
        ``ExecutionContext`` to read one from, and this tool has no ``undo()``."""
        self._store_ref = store_ref

    # --- what the dispatch layer asks about a call ---------------------------

    def is_destructive(self, args: dict) -> bool:
        """True — one ordinary card in OPEN, every time.

        HIGH would answer True by default (``tools.base.call_is_destructive``);
        saying it out loud is what keeps the answer from changing silently if the
        tier is ever revisited. Destructive here means "the computer's state
        changes and the person should see it happen", not "this is dangerous" —
        the danger was the arming."""
        return True

    def affected_path(self, args: dict) -> str | None:
        """None — not path-bounded, for ``arm_automation``'s reason: the file that
        goes away belongs to the shell, not to a path this call names."""
        return None

    # NO ``command_text``. See the module docstring — declaring one would let the
    # hardline denylist refuse a DISARM, which is how a person ends up unable to
    # switch off the job they most want switched off.

    def permission_detail(self, args: dict) -> str | None:
        """The automation's NAME, redacted — the panel and the card both show it."""
        row = self._row(args)
        if row is None:
            return None
        return redact(row.name.strip()).text or None

    # --- the door ------------------------------------------------------------

    def _lookup(self, args: dict) -> AutomationLookup:
        """Which automation this call named — the one place this tool asks, and the
        SAME resolution its sibling uses (``resolve_automation``): the id first, then
        an exact name. Shared rather than copied, so switching one off can never land
        on a different row from the one that was switched on."""
        store = self._store_ref()
        if store is None:
            return AutomationLookup()
        return resolve_automation(store, args.get("id"))

    def _row(self, args: dict) -> Automation | None:
        """The automation this call names, or None. Never raises."""
        return self._lookup(args).row

    def arming_refusal(self, args: dict) -> str | None:
        """The door, asked BEFORE the gate — two questions, not four.

        No denylist check (the module docstring says why) and no schedule check: a
        job whose schedule this vocabulary cannot read is exactly the job somebody
        most wants to be able to switch off. What remains is the platform and
        whether Addison still knows which label to name."""
        if self._store_ref() is None:
            return _NOT_READY
        if not arming_is_supported():
            return _ONLY_ON_A_MAC
        found = self._lookup(args)
        if found.ambiguous:
            return _AMBIGUOUS_NAME
        if found.row is None:
            return _NO_SUCH_AUTOMATION
        return None

    # NO ``arming_card``. Its absence is what makes this an ordinary destructive
    # card rather than the keyword gate: the gate reads the tool for one
    # (``permissions.gate.call_arming_card``), and finding none takes the ordinary
    # per-invocation path.

    # --- the act -------------------------------------------------------------

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult:
        if context.policy_mode is not PolicyMode.OPEN:
            return ToolResult(success=False, content=_ONLY_IN_DEVELOPER)
        refusal = self.arming_refusal(args)
        if refusal is not None:
            return ToolResult(success=False, content=refusal)
        row = self._row(args)
        if row is None:  # pragma: no cover - arming_refusal covers it
            return ToolResult(success=False, content=_NO_SUCH_AUTOMATION)
        if context.shell_bridge is None:
            return ToolResult(success=False, content=_NO_SHELL)
        try:
            result = context.shell_bridge.disarm_automation(row.label)
        except RuntimeError as exc:
            return ToolResult(success=False, content=str(exc))
        if not result.get("ok"):
            error = result.get("error")
            return ToolResult(
                success=False,
                content=(
                    str(error) if isinstance(error, str) and error.strip() else _COULDNT_DISARM
                ),
            )
        # NO SNAPSHOT, and therefore nothing for the UndoManager to replay. That is
        # the point rather than an omission: see the module docstring.
        return ToolResult(success=True, content=_disarmed_text(row))
