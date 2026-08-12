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
    irreversible OPEN-only tool (``run_command``, ``disarm_automation``).
``write_project_file`` forced the split and no longer occupies it: it needed to be
``open_only`` (hidden from SAFE) AND undo-ENFORCED at once, and on 2026-08-11 it
left the SAFE boundary altogether (Simple can change a file behind a card —
docs/SAFETY.md). The pairing it forced is still live and still load-bearing:
``create_automation`` and ``arm_automation`` are both ``open_only`` with a real
``undo()``, so an edit dropping either method fails registration. ``dev_only=True``
stays as a convenience alias that sets BOTH — the exact shape ``run_command`` needs.

**THREAD CONFINEMENT IS AN INVARIANT HERE, and nothing in this file locks.** Until
the MCP client arrived, every registration happened once at startup
(``main.build_registry``) and these dicts were read-only for the life of the
process. ``mcp.refresh`` mutates them while the app is running. What makes that
safe is that every mutator AND every reader is on ONE thread: ``main.py`` routes
``mcp.*`` onto the ``turn-worker`` queue, and turns, routine steps, widget runs and
snapshot restores are worker jobs too, so a refresh can never interleave with a
dispatch that is walking ``_tools``. The thing that would break it is answering an
``mcp.*`` method — or any later method that registers or unregisters — INLINE on
the read loop, the way ``permission.respond`` and ``model.setRoleForNextMessage``
are answered: a registration landing mid-``visible_tools`` is a mutated-during-
iteration error inside somebody's turn, and a half-applied refresh is a registry
that disagrees with the catalog about what exists. ``tests/test_mcp_servers.py``
pins the routing so that a new handler cannot quietly take the inline path.

Two further dimensions arrived with the MCP client (step 7 phase 2), and BOTH are
about tools that came from OUTSIDE this codebase. Every native tool is registered
once at startup by ``build_registry`` and stays for the life of the process; a
discovered tool belongs to a server that can change its mind or be removed:
  * ``removable`` — this id may later be ``unregister``ed. Off by default, and
    ``unregister`` REFUSES anything without it, so no future caller can quietly
    take a native tool out of the registry the safety model is built on.
  * ``not_callable`` — the person may see the tool; the MODEL never does, and
    dispatch refuses it (``refuse_if_not_callable``). Step 7 phase 2 discovered
    tools it could not yet run, and "we simply won't call it" is not a mechanism —
    this is. **Nothing sets it today**: phase 3 (2026-08-07) made MCP tools
    callable, so the flag is now the mechanism its own phase constant operates
    through (``mcp_catalog.MCP_TOOLS_ARE_CALLABLE``) and the shape the next
    discovered-before-its-dispatch-exists tool inherits. It is not dead code with a
    test — it is a boundary whose current occupant graduated.

A fifth arrived with step 8 phase 3, and it is about WHERE a call may start rather
than about who wrote the tool:
  * ``live_only`` — this tool may run from the live conversation and from nowhere
    that replays a SAVED spec. ``refuse_if_live_only`` is checked by the routine
    engine and the widget rail, above the gate. It exists because the arming card
    carries a preview and a typed code, and a stored one-click spec that could raise
    one invites answering it on autopilot — the reflex the code exists to break
    (docs/step-8-automation-plan.md §5.10). Note this is a NARROWING of what a
    routine may do relative to live chat, which is SAFE invariant 3's permitted
    direction.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode
from agent_core.tools.base import RiskTier, Tool, ToolDefinition

# Said when a dev-only tool is reached outside OPEN mode. Plain language, and the
# same register as run_command's own refusal — the person is being told a whole
# capability belongs to another profile, not shown an enforcement detail.
DEV_ONLY_REFUSAL = "That's only available in the Developer profile."

# Said when a tool that has been DISCOVERED but not yet wired for dispatch is
# named anyway. Written for step 7 phase 2's MCP tools and outlived them: phase 3
# gave those tools a dispatch, and this stays as the sentence for the next kind
# that arrives without one. Deliberately says nothing about MCP or about a phase —
# it is one fact told plainly, and a surface that ever prints it again should print
# these exact words.
NOT_CALLABLE_REFUSAL = "Addison can see this tool but can't use it yet."

# Said when a STORED, REPLAYABLE spec — a routine step, a widget's Run pill — names
# a tool that may only run where a person is present and reading (step 8 phase 3,
# plan §5.10). Today that is arming and disarming an automation, whose card carries
# a preview and a typed code: a saved spec that could raise one mid-run invites
# answering it on autopilot, which is the reflex the ceremony exists to break.
#
# It says what to do instead, because a refusal with no next move is a dead end the
# model reports back as a blocked task — and the next move is genuinely easy (ask
# for it in the conversation). No jargon: not "live_only", not "dispatch site".
LIVE_ONLY_REFUSAL = (
    "Switching an automation on or off is something you do yourself — just ask for "
    "it in the conversation, and Addison will show you exactly what will run."
)

# Said when a dispatch path is handed an id NOTHING is registered under. Both ways
# that happens are ordinary rather than exceptional: a model naming a tool from
# earlier in a transcript after a refresh, a removal, a failed check or a snapshot
# restore took that id out; and a saved routine step naming a discovered tool in a
# session that has not gone looking for it yet (a tool server's catalog lives in
# memory and is rebuilt on demand, so nothing is registered after a restart until
# somebody presses Check now).
#
# The id itself is deliberately NOT in the sentence: `mcp:Design docs:search` is
# Addison's internal spelling of somebody else's tool, and it answers no question
# a person reading a routine's step log has.
UNKNOWN_TOOL_REFUSAL = "That tool isn't available any more, so Addison didn't run it."


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._open_only: set[str] = set()   # tool ids only visible/runnable in OPEN mode
        self._removable: set[str] = set()   # ids `unregister` may take out (MCP only)
        self._not_callable: set[str] = set()  # ids hidden from the model + refused at dispatch
        self._live_only: set[str] = set()   # ids a stored/replayable spec may not name

    def register(
        self,
        tool: Tool,
        *,
        dev_only: bool = False,
        open_only: bool = False,
        allow_missing_undo: bool = False,
        removable: bool = False,
        not_callable: bool = False,
        live_only: bool = False,
    ) -> None:
        """Register a tool along the two independent OPEN dimensions (R3).

        ``open_only`` hides the tool from the SAFE view (``visible_tools(SAFE)`` /
        ``list_for_model``) and makes ``refuse_if_dev_only_outside_open`` refuse it
        outside OPEN. ``allow_missing_undo`` is the ONLY thing that exempts a
        non-LOW tool from the undo-at-registration check. ``dev_only=True`` is the
        convenience alias that sets BOTH (``run_command``'s exact shape).

        The undo-at-registration check still raises for any non-LOW tool without a
        real ``undo()`` UNLESS ``allow_missing_undo`` — so ``create_automation``
        (``open_only=True, allow_missing_undo=False``) is hidden from SAFE yet stays
        undo-ENFORCED, the case the split exists for.

        ``removable`` and ``not_callable`` are the two externally-sourced-tool
        dimensions (see the module docstring). Both default OFF, so every native
        registration keeps exactly the properties it had before they existed.

        ``live_only`` is the fifth and narrowest dimension (step 8 phase 3): the
        tool may run from the LIVE conversation and from nowhere that replays a
        saved spec. It is orthogonal to every other flag — the two arming tools are
        ``live_only`` and one of them is undo-ENFORCED while the other takes the
        undo waiver — and it is checked at the two stored-spec dispatch sites by
        ``refuse_if_live_only``, above the gate."""
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
        if removable:
            self._removable.add(tool.definition.id)
        if not_callable:
            self._not_callable.add(tool.definition.id)
        if live_only:
            self._live_only.add(tool.definition.id)

    def get(self, tool_id: str) -> Tool:
        try:
            return self._tools[tool_id]
        except KeyError:
            raise KeyError(f"No tool registered with id '{tool_id}'.") from None

    def find(self, tool_id: str) -> Tool | None:
        """The tool, or None when nothing is registered under this id.

        ``get`` RAISES, which is right for a caller that has already established the
        id exists. A DISPATCH path has not: the ids it is handed come from a model's
        transcript and from routine steps saved in an earlier session, and both
        outlive the registration they were written against — every ``mcp:`` id
        leaves the registry on a refresh, a removal, a failed check or a snapshot
        restore, and after a restart none of them is back until somebody asks for a
        check. So both paths resolve through this and turn a miss into
        :data:`UNKNOWN_TOOL_REFUSAL`, the shape every other failure on those paths
        already takes. Raising there costs a turn its tool_result (which the
        provider then rejects for the rest of the session) or leaves a routine run
        recorded as 'running' forever, because neither path's cleanup runs."""
        return self._tools.get(tool_id)

    def has(self, tool_id: str) -> bool:
        """Whether this id is taken — asked BEFORE registering a discovered tool.

        ``register`` already raises on a duplicate, but a server offering a name
        that collides with a native tool is an ordinary, expected event that must
        cost one skipped row and a plain sentence, not an exception the caller has
        to catch to keep the other ninety-nine tools."""
        return tool_id in self._tools

    def unregister(self, tool_id: str) -> None:
        """Take a REMOVABLE tool back out. Raises for anything else.

        Discovery is re-runnable, so a server's registrations have to be replaceable
        — and removing a server has to remove its tools, or ``mcp.remove`` would
        leave a stranger's ids behind in the registry every dispatch path consults.

        THE REFUSAL IS THE POINT. This is the first way anything has ever left the
        registry, and an unconditional version would be a supported route to
        deleting ``save_file``'s undo-enforced registration, or ``snapshot_now``, at
        runtime. Only ids registered with ``removable=True`` are eligible, which
        today is exactly the MCP-discovered set; a native tool raises."""
        if tool_id not in self._removable:
            raise ValueError(
                f"Tool '{tool_id}' was not registered as removable and cannot be "
                "unregistered. Only externally-discovered tools may be."
            )
        self._tools.pop(tool_id, None)
        self._open_only.discard(tool_id)
        self._not_callable.discard(tool_id)
        self._live_only.discard(tool_id)
        self._removable.discard(tool_id)

    def is_dev_only(self, tool_id: str) -> bool:
        """True for an OPEN-only tool — hidden from SAFE and refused at dispatch
        outside OPEN. Named for its original single dimension; since R3 it reports
        the ``open_only`` (visibility) set, which is what the SAFE boundary keys off
        (``run_command``, the automation tools and every ``mcp:`` tool belong to it;
        the step-5 file tools did until 2026-08-11)."""
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

    def refuse_if_not_callable(self, tool_id: str) -> str | None:
        """The SECOND layer under a discovered-but-not-wired tool: a plain refusal
        sentence when ``tool_id`` was registered ``not_callable``, else None.

        Mode-independent, unlike its dev-only sibling — this is not "wrong profile",
        it is "no dispatch exists". ``visible_tools`` already keeps these ids away
        from the model, and this is the layer that holds when something names one
        anyway: a stale transcript replayed after a refresh, a routine step saved
        before the tool went away, a model that guessed the id. Called by BOTH
        dispatch paths (the orchestrator turn and the routine step) BEFORE the gate
        and before any network is touched, exactly as the dev-only check is —
        a boundary only one path enforces is not a boundary."""
        if tool_id in self._not_callable:
            return NOT_CALLABLE_REFUSAL
        return None

    def refuse_if_live_only(self, tool_id: str) -> str | None:
        """A plain refusal sentence when a STORED, REPLAYABLE spec names a tool that
        may only run in the live conversation, else None (step 8 phase 3, §5.10).

        Mode-independent, like its ``not_callable`` sibling and unlike the dev-only
        one: this is not "wrong profile", it is "not from here". Called by the
        routine engine and by the widget rail — the two places a saved spec starts a
        tool call — BEFORE the gate, so the ceremony's card is never raised by a
        one-click replay in the first place.

        THE MARKER IS NEVER THE ENFORCEMENT (CLAUDE.md's artifact rule, applied to
        dispatch): a routine listing this step, or a frontend offering it, changes
        nothing — this refusal is what decides, and it wins.

        Why a registration flag rather than a list of ids at each site: a boundary
        spelled twice is a boundary that holds until the two spellings drift, and
        the next tool that needs a person in the room should inherit this by saying
        so at registration rather than by somebody remembering two call sites."""
        if tool_id in self._live_only:
            return LIVE_ONLY_REFUSAL
        return None

    def visible_tools(self, mode: PolicyMode) -> list[ToolDefinition]:
        """The tool definitions the model may call under ``mode``.

        SAFE mode hides every ``open_only`` tool — the SAFE view is byte-for-byte the
        historical registry contents. OPEN mode surfaces all of them.

        A ``not_callable`` tool is absent from EVERY mode's view. This is the list
        that is sent to the model as its tool definitions, so an id in it is an
        invitation, and a tool with no dispatch behind it must never be invited: the
        person seeing it on the Tools surface is a different thing from the model
        being offered it. Step 7 phase 2 was that case and phase 3 ended it — an
        ``mcp:`` id now appears here in OPEN (never in SAFE, which is ``open_only``'s
        job), which is also the moment a server's text began reaching a model's
        context and the plan's §7 deferral became load-bearing rather than
        theoretical."""
        return [
            tool.definition
            for tool_id, tool in self._tools.items()
            if (mode is PolicyMode.OPEN or tool_id not in self._open_only)
            and tool_id not in self._not_callable
        ]

    def list_for_model(self) -> list[ToolDefinition]:
        """The SAFE view (dev_only tools excluded), by its historical name.

        **Nothing in ``agent_core/`` calls this any more** — the orchestrator, the
        routine engine and the RPC layer all resolve the live view per turn via
        ``visible_tools(mode)``. Its remaining callers are tests, where naming the
        SAFE view directly is the clearer way to say what is being asserted. Kept
        for them; delete it the day they stop asking."""
        return self.visible_tools(PolicyMode.SAFE)
