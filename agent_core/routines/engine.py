"""Routine execution engine — engineering-spec §6.4.

CRITICAL INVARIANT (§6.4, §8.5): the engine uses the SAME ToolRegistry and
PermissionGate instances as the live orchestrator. A Routine is a shortcut for
re-issuing a sequence of tool calls, NOT a way to bypass the permission system.
It never has, and must never be given, access beyond what the user has already
granted in live conversation. No auto-escalation.

Template values are substituted strictly as DATA (§6.1/§6.2): a placeholder is
replaced by a string, never parsed, evaluated, or executed. There is no code
path here that interprets a resolved value.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from agent_core.permissions.gate import PermissionGate, PermissionStatus
from agent_core.policy import PolicyMode, TurnSurface
from agent_core.routines.model import Routine, RoutineStep
from agent_core.routines.taint import RunTaint
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import (
    ExecutionContext,
    Tool,
    ToolResult,
    call_affected_path,
    call_is_destructive,
    call_permission_detail,
    default_forbidden_check,
)
from agent_core.tools.registry import UNKNOWN_TOOL_REFUSAL, ToolRegistry

# Confinement refusal for a routine step (step 5, D3), kept identical to the live
# loop's so the same message shows wherever a path-bounded tool runs outside trust.
_OUTSIDE_TRUST = (
    "That file is outside the folders you've trusted, so Addison left it alone. "
    "Trust its folder first if you want Addison to work with it."
)

# A command step (RoutineStep.command set) runs through this dev-only tool, so it
# hits the exact same registry + gate path — and destructive-prompt rule — as a
# live run_command call. No new execution surface is added to the engine itself.
_RUN_COMMAND_TOOL_ID = "run_command"

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)\s*\}\}")


# The routine's answer, capped where it is BUILT rather than at the boundary that
# shows it — the same rule ``call_permission_detail`` follows for the activity
# line. A step can return a whole page of text; what a person needs back from
# "Quick Sums" is the sum, and a panel is not a transcript.
MAX_ROUTINE_ANSWER_CHARS = 2000


@dataclass
class RoutineRunResult:
    run_id: str
    status: str                                   # 'completed' | 'failed' | 'cancelled'
    step_results: dict = field(default_factory=dict)
    detail: str = ""
    # THE THING THE PERSON RAN IT FOR (owner decision 2026-08-12). A routine's
    # steps are tool calls, so the last text a run produced is the last SUCCEEDING
    # step's content — Quick Sums' 6016. It used to exist only inside
    # ``step_results`` keyed by step id, which no surface read, so a run reported
    # "Done — every step finished" and its answer appeared nowhere.
    #
    # Empty string when nothing textual came back (a run of nothing but file
    # writes), which is not a failure and must not read as one.
    answer: str = ""


def topologically_sorted(steps: list[RoutineStep]) -> list[RoutineStep]:
    """Order steps so every step's ``depends_on`` come first. Raises on a cycle."""
    by_id = {s.step_id: s for s in steps}
    ordered: list[RoutineStep] = []
    visited: dict[str, int] = {}  # 0 = visiting, 1 = done

    def visit(step: RoutineStep) -> None:
        state = visited.get(step.step_id)
        if state == 1:
            return
        if state == 0:
            raise ValueError(f"Cycle detected in routine at step '{step.step_id}'.")
        visited[step.step_id] = 0
        for dep in step.depends_on:
            if dep not in by_id:
                raise ValueError(f"Step '{step.step_id}' depends on unknown step '{dep}'.")
            visit(by_id[dep])
        visited[step.step_id] = 1
        ordered.append(step)

    for step in steps:
        visit(step)
    return ordered


def resolve_template(template: dict, variables: dict, step_results: dict) -> dict:
    """Substitute ``{{variable}}`` and ``{{step_id.result}}`` placeholders.

    Resolution is pure string substitution over the template's values (including
    nested dicts/lists) — a resolved value is data handed to a tool, never code
    (§6.2). An unknown placeholder raises ValueError with a plain-language
    message so the run fails loudly instead of running a half-filled step.
    """

    def lookup(name: str) -> str:
        if name.endswith(".result"):
            step_id = name[: -len(".result")]
            if step_id in step_results:
                return str(step_results[step_id].content)
            raise ValueError(
                f"This step needs the result of '{step_id}', which hasn't run yet."
            )
        if name in variables and variables[name] is not None:
            return str(variables[name])
        raise ValueError(f"This routine needs a value for '{name}' before it can run.")

    def resolve_value(value):
        if isinstance(value, str):
            # A value that IS exactly one placeholder substitutes cleanly;
            # otherwise substitute inside the surrounding text.
            return _PLACEHOLDER.sub(lambda m: lookup(m.group(1)), value)
        if isinstance(value, dict):
            return {k: resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_value(v) for v in value]
        return value

    return {key: resolve_value(value) for key, value in template.items()}


class RoutineEngine:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        permission_gate: PermissionGate,
        undo_manager: UndoManager,
        shell_bridge=None,
        on_ask_user: Callable[[RoutineStep, str, str], bool] | None = None,
        store=None,
        on_activity=None,
        on_step=None,
        guards_provider=None,
        trust_check=None,
        forbidden_check=None,
        on_tool_audit=None,
        trusted_roots=None,
    ) -> None:
        # SAME instances as the live orchestrator — never private copies (§6.4).
        self.tool_registry = tool_registry
        self.permission_gate = permission_gate
        self.undo_manager = undo_manager
        self.shell_bridge = shell_bridge
        # Workspace-trust confinement (step 5, D3): the SAME resolver the live loop
        # uses, so a routine can never touch a path outside trust that the chat
        # couldn't. Default refuses everything (the file tools aren't routine-exposed
        # in v1, so this is defence-in-depth). NOTE: a routine command step / any
        # step still passes trusted=False to the gate unconditionally (D5) — a
        # persisted one-click spec never skips a card.
        self._trust_check = trust_check or (lambda path: False)
        # The hardline denylist (step 5.5, item 3), wired exactly as trust_check is:
        # it needs the LIVE data directory, which only the server knows. Same
        # instance-shared discipline as everything else here — a routine must get
        # the same answer the live loop gets, never a second derivation of it.
        self._forbidden_check = forbidden_check or default_forbidden_check
        # The audit sink (step 5.5, item 4) — the SAME one the live loop writes to,
        # for the same reason the gate and registry are shared: a routine's tool
        # calls must be as visible after the fact as a conversation's. A routine is
        # persisted, one-click and model-authorable, so "what did that routine
        # actually do?" is a question the log has to be able to answer.
        self._on_tool_audit = on_tool_audit
        # The live trusted roots, as a zero-arg callable (step 5.5, item 2). Same
        # resolver the live loop uses: a routine's command must be sandboxed by the
        # same allowlist the chat's would be, never a wider one.
        self._trusted_roots = trusted_roots
        # The effective GuardConfig provider (Custom profile, D3) — the SAME
        # resolution function the live loop uses, so a routine can never out- or
        # under-permission the conversation. None/absent -> the unguarded gate.
        self._guards_provider = guards_provider or (lambda: None)
        # Same signature and same consumer as the orchestrator's (tool_id, label,
        # detail) — the Activity Panel. A routine runs the same tools through the
        # same gate, so a saved routine containing a page-read step reaches a site
        # exactly as a live turn does; without this it did so with nothing on screen
        # naming it, which would leave the destination visible on the path the user
        # is watching and invisible on the path that runs by itself.
        self.on_activity = on_activity or (lambda tool_id, label, detail=None: None)
        # PER-STEP PROGRESS (owner decision 2026-08-12) — one dict per step, as the
        # step starts and again when it ends. Its consumer is the small panel the
        # Settings routine row opens while a run is going, which is the same idiom
        # as the chat's "Addison's work": a run that only reports its terminal row
        # is a run nobody can watch, and a routine is the path that runs with
        # nobody watching by construction.
        #
        # IT CARRIES TOOL IDS, NEVER LABELS. Every step emits one of these,
        # including the branches that refuse BEFORE a tool is in hand (an id
        # nothing is registered under is the commonest of them), so an event that
        # promised a label would have to invent one for exactly the steps a person
        # most needs named. The RPC layer holds the registry and labels the id
        # there (``main.py::_label``, which falls back to the id itself) — the same
        # path the OPEN-mode auto-grant notification takes.
        self.on_step = on_step or (lambda event: None)
        # on_ask_user(step, run_id, message) -> True to continue past the failed
        # step, False to stop. Rendered by the frontend with the same card
        # pattern as a permission request (§6.2). Default: stop.
        self._on_ask_user = on_ask_user or (lambda step, run_id, message: False)
        self._store = store   # optional: writes the routine_runs log (§6.4)

    def _audit(
        self, routine, tool_id, detail, mode, destructive, outcome, redacted=None
    ) -> None:
        """One tool-decision row for a routine step. Best-effort, like the live
        loop's: history must never be the reason a run dies. ``conversation_id`` is
        None — a routine is not a conversation — and the routine is identifiable
        from ``routine_runs`` by timestamp.

        ``redacted`` arrives only from a tool that scrubbed its own output (MCP
        dispatch, step 7 phase 3). The live loop ALSO classifies ordinary tool
        output here by re-running the redactor; this path does not, and the
        difference is honest rather than an omission — a routine's results do not
        cross the orchestrator's send boundary, so there is no "on its way to the
        model" for this column to describe except where a tool redacted itself."""
        if self._on_tool_audit is None:
            return
        try:
            self._on_tool_audit(
                {
                    "id": str(uuid.uuid4()),
                    "conversation_id": None,
                    "tool_id": tool_id,
                    "detail": detail,
                    "mode": mode.value if hasattr(mode, "value") else str(mode),
                    "destructive": bool(destructive),
                    "outcome": outcome,
                    # KINDS, deduplicated and sorted — the live loop's rule, for the
                    # same reason: one entry per kind, never one per match, in a
                    # table that is never pruned.
                    "redacted": ", ".join(sorted(set(redacted))) if redacted else None,
                    "created_at": int(time.time()),
                }
            )
        except Exception:
            pass

    def run(
        self,
        routine: Routine,
        variable_values: dict[str, str],
        mode: PolicyMode = PolicyMode.SAFE,
        surface: TurnSurface = TurnSurface.DESK,
    ) -> RoutineRunResult:
        # ``surface`` (policy.py) is WHERE the run was started from. DESK is every
        # caller today and leaves this path byte-identical; it is threaded through
        # to ``_pre_gate_refusal`` so the remote floor is enforced at BOTH dispatch
        # sites rather than at one — a boundary only one path enforces is not a
        # boundary, and the phase that admits a remote tool must not also have to
        # remember to come back here (messaging channels, plan §3.6).
        # ``mode`` (policy.py) is the live policy mode: SAFE (default) is the
        # historical behaviour; OPEN thins the gate to prompt only for destructive
        # steps and lets command steps run. main.py refuses to run a dev-created
        # routine in SAFE mode before ever reaching here, so SAFE never sees a
        # command step. The SAME gate/registry instances as the live loop are used
        # in both modes — a routine can never out-permission the user (§8.5).
        run_id = str(uuid.uuid4())
        # One guard posture for the whole run (Custom profile, D3), same as the
        # live turn: resolved once, passed to every step's authorize call.
        guards = self._guards_provider()
        step_results: dict[str, ToolResult] = {}
        step_log: list[dict] = []
        # THE ONE FLOW EDGE (owner decision 4B, 2026-08-15). Per run, in memory,
        # gone when the run returns — file-read output going into a network-bound
        # step gets one extra line on that step's card. What it is and what it
        # deliberately does not catch live in ``routines/taint.py``.
        taint = RunTaint()
        context = ExecutionContext(
            conversation_id=f"routine:{routine.id}",
            shell_bridge=self.shell_bridge,
            policy_mode=mode,
            trusted_roots=self._trusted_roots,
        )

        # Variable defaults fill anything the caller didn't supply.
        variables = {v.name: v.default for v in routine.variables}
        variables.update({k: v for k, v in (variable_values or {}).items() if v is not None})

        self._log_run_started(run_id, routine.id)

        try:
            ordered = topologically_sorted(routine.steps)
        except ValueError as exc:
            return self._finish(run_id, "failed", step_results, str(exc), step_log)

        total = len(ordered)
        # The last text the run produced (owner decision 2026-08-12). Kept as the run
        # walks rather than reconstructed at the end: ``step_results`` holds refusals
        # beside answers, so "the last entry" is not the same question.
        answer = ""

        for index, step in enumerate(ordered):
            # A command step (OPEN mode only) runs through run_command; an ordinary
            # step names its own tool. Either way the SAME registry + gate handle it.
            if step.command is not None:
                tool_id = _RUN_COMMAND_TOOL_ID
                args_template = {"command": step.command}
            else:
                tool_id = step.tool_id
                args_template = step.args_template

            # Announced BEFORE the first thing that can refuse it, so a step that
            # never gets off the ground is still a step the panel names.
            self._step_event(run_id, routine, index, total, step, tool_id, "running")

            try:
                resolved_args = resolve_template(args_template, variables, step_results)
            except ValueError as exc:
                self._step_event(
                    run_id, routine, index, total, step, tool_id, "failed", str(exc)
                )
                return self._finish(run_id, "failed", step_results, str(exc), step_log, answer)

            # AN ID NOTHING IS REGISTERED UNDER — the live loop's twin, and the case
            # a saved routine meets most often. A step keeps the tool id it was
            # written with, and an `mcp:` id is registered only for as long as this
            # session's last check says a server offers it: a restart, a removal, a
            # failed check or a snapshot restore all leave a step naming nothing.
            # Raising here would skip `_finish` and leave this run recorded as
            # 'running' with no completed_at, forever — the exact failure the
            # execute branch below documents — so it is a FAILED STEP like every
            # other refusal, and `on_failure` still decides what happens next.
            # `detail` is None and `destructive` False: with no tool there is
            # nothing to ask either question of.
            #
            # It stands apart from the guard table below for one reason only: every
            # per-call fact that table works from needs a tool to ask. The FAILURE
            # is shaped in the one shared place, like all the others.
            tool = self.tool_registry.find(tool_id)
            if tool is None:
                self._audit(routine, tool_id, None, mode, False, "not_callable")
                terminal = self._record_step(
                    ToolResult(success=False, content=UNKNOWN_TOOL_REFUSAL),
                    step_results=step_results, step_log=step_log, index=index, step=step,
                    run_id=run_id, routine=routine, total=total, tool_id=tool_id,
                    answer=answer,
                )
                if terminal is not None:
                    return terminal
                continue
            # Per-call destructiveness, resolved ONCE for every branch below — the
            # refusal branches used to hard-code a literal, which described the
            # branch rather than the call (the live loop had the same defect).
            destructive = call_is_destructive(tool, resolved_args)
            # The step's path, RESOLVED ONCE for the whole step — the live loop's twin
            # (KNOWN-GAPS, closed 2026-08-08). Every audit row below that names a file
            # is built from this value, and so is the card; confinement checks the same
            # one and hands it to `execute`. It sat at the confinement branch until
            # then, so the rows above it re-resolved and could name a different file
            # than the one the step acted on. None for a command step, which is what
            # leaves those completely unaffected.
            affected = call_affected_path(tool, resolved_args)
            # THE PRE-GATE GUARDS, asked in ONE call and answered in ONE block
            # (KNOWN-GAPS, closed 2026-08-22). Each used to shape its own refusal and
            # re-implement abort / ask_user / skip inline, five copies that agreed only
            # because each was written to match its neighbours.
            refusal = self._pre_gate_refusal(
                tool, tool_id, resolved_args, affected, mode, surface
            )
            if refusal is not None:
                message, outcome, audit_detail = refusal
                self._audit(routine, tool_id, audit_detail, mode, destructive, outcome)
                terminal = self._record_step(
                    ToolResult(success=False, content=message),
                    step_results=step_results, step_log=step_log, index=index, step=step,
                    run_id=run_id, routine=routine, total=total, tool_id=tool_id,
                    answer=answer,
                )
                if terminal is not None:
                    return terminal
                continue
            context.resolved_path = affected
            # Mode-aware authorization (policy.py): SAFE prompts for every
            # not-yet-granted step; OPEN auto-allows non-destructive steps and
            # prompts PER INVOCATION for destructive ones (a destructive command
            # stops to ask every time, card showing the exact resolved command).
            # Routines NEVER auto-escalate — same gate as §4.3. trusted=False
            # UNCONDITIONALLY (D5): a persisted, one-click, model-authorable spec
            # must never skip a card the way a live confined edit does.
            # ``destructive`` was resolved above the refusal branches, so the gate
            # and every audit row agree on one answer per step.
            # Asked once and used twice, exactly as the live loop does it: the
            # permission card and the Activity Panel must describe the SAME step.
            detail = call_permission_detail(tool, resolved_args, affected)
            # THE FLOW LINE, decided AT THE MOMENT OF SUBSTITUTION — here, where
            # the resolved values are known — and never at import or save time,
            # when the arguments are still placeholders and there is nothing to
            # compare. It rides the delete preview's mechanism (``preview``), so
            # it is one extra plain line on the card and reaches nothing else: not
            # ``detail``, not the audit row below, not a grant. ``force_card``
            # makes sure there IS a card to carry it, in every profile — the card
            # is the control in both.
            taint_line = taint.line_for(tool_id, resolved_args)
            status = self.permission_gate.authorize(
                tool_id,
                mode=mode,
                destructive=destructive,
                detail=detail,
                guards=guards,
                trusted=False,
                preview=taint_line,
                force_card=taint_line is not None,
            )
            if status == PermissionStatus.DENIED:
                self._audit(routine, tool_id, detail, mode, destructive, "denied")
                # The log's word is "permission denied"; the PERSON reads the same
                # sentence the run's terminal state uses, so the step that stopped
                # the run and the reason it stopped say one thing.
                self._step_done(
                    step_log, index, step, "permission denied",
                    run_id=run_id, routine=routine, total=total, tool_id=tool_id, ok=False,
                    message="You said no, so this step didn't run.",
                )
                return self._finish(
                    run_id, "failed", step_results, "You declined a permission it needs.",
                    step_log, answer,
                )

            # A tool/bridge failure is a FAILED STEP, never a crashed run — mirror
            # the live orchestrator (§4.4). Letting it propagate would skip the
            # on_failure policy below AND leave the routine_runs log stuck at
            # 'running' (its _finish never runs). A shell-bridge refusal (e.g.
            # save_file's "A file with that name is already there") raises
            # RuntimeError with a plain user-ready sentence; anything else collapses
            # to one plain message.
            # Announced only once the step is actually granted, so a declined step
            # is never reported as something Addison did.
            self.on_activity(tool_id, tool.definition.label, detail)
            try:
                result = tool.execute(resolved_args, context)
            except RuntimeError as exc:
                result = ToolResult(success=False, content=str(exc))
            except Exception:
                result = ToolResult(success=False, content="That step didn't work.")
            # AUDITED AFTER EXECUTION, the live loop's shape (step 7 phase 3). The
            # row used to be written before the call, which cost it the two things
            # only the result knows: what the redactor took out of THIS step's
            # output, and whether an approved call reached anything at all. Nothing
            # between the gate and here can return early — both failure shapes are
            # caught above — so the row is written on exactly the runs it was
            # before. Both fields are empty for every native tool.
            self._audit(
                routine, tool_id, detail, mode, destructive,
                result.audit_outcome or "granted",
                redacted=result.redacted_kinds or None,
            )
            # Remembered only from a step that SUCCEEDED: a refusal's content is a
            # sentence Addison wrote, not anything out of a file.
            if result.success:
                taint.record(tool_id, resolved_args, affected, result.content)
            if result.snapshot:
                result.snapshot.tool_call_id = f"{run_id}:{step.step_id}"
                self.undo_manager.record(result.snapshot)   # Routine runs are undoable too
            # THE ANSWER, kept from the last step that actually produced text. A
            # later step that returns nothing (a file write, a snapshot) does not
            # erase it — a person asked for the sum, not for the last thing to
            # happen after it.
            if result.success:
                text = str(result.content or "").strip()
                if text:
                    answer = text[:MAX_ROUTINE_ANSWER_CHARS]
            # The same block every refusal above goes through — a tool that failed
            # honestly is not a different kind of failed step than one that was
            # refused before it could run.
            terminal = self._record_step(
                result,
                step_results=step_results, step_log=step_log, index=index, step=step,
                run_id=run_id, routine=routine, total=total, tool_id=tool_id, answer=answer,
            )
            if terminal is not None:
                return terminal
            # "skip" (and an ask_user the person answered "keep going" to) falls
            # through to the next step.

        return self._finish(run_id, "completed", step_results, "", step_log, answer)

    # --- the pre-gate guards, in one table (KNOWN-GAPS, closed 2026-08-22) ----
    def _pre_gate_refusal(
        self,
        tool: Tool,
        tool_id: str,
        resolved_args: dict,
        affected: str | None,
        mode: PolicyMode,
        surface: TurnSurface = TurnSurface.DESK,
    ) -> tuple[str, str, str | None] | None:
        """Every reason a step is refused BEFORE the gate sees it, asked in order.

        Returns ``(message, audit_outcome, audit_detail)`` for the first guard that
        refuses, or None when the step may go on to the gate. It decides nothing
        about what happens NEXT: the caller shapes one failed step out of whatever
        comes back and ``_record_step`` reads ``on_failure`` once, for these and for
        a tool that failed honestly alike. That was the point of collecting them —
        each guard used to carry its own copy of the abort / ask_user / skip
        handling, and five copies agree only until someone adds a fourth policy.

        ORDER IS BEHAVIOUR. The first refusal wins and the rest are never asked, so
        a step naming a dev-only tool in SAFE reports that rather than whatever the
        denylist would have said about it. The order below is the order the guards
        were written in.
        """
        # SAFE-1 at dispatch, before the gate: a routine step naming a dev-only
        # tool cannot run outside OPEN, whoever wrote that tool. Refusing here
        # rather than inside execute also means the person is never asked to
        # approve something that was never going to run. Shaped as a FAILED
        # STEP (not a failed run) so on_failure still decides what happens next
        # — byte-for-byte what run_command's own refusal already produced.
        #
        # The live loop audits this branch; this one did not, so a routine step
        # naming a dev tool in SAFE was the one refusal in the tree that left no
        # record at all — and a routine is the path that runs without anyone
        # watching. The detail is None to match the live loop's dev_only row
        # exactly: nothing about the call was examined.
        # NOT FROM A PHONE (messaging channels phase 2, plan §3.6). FIRST, for the
        # live loop's reason exactly: "not from there" is the true reason, and a
        # step naming a dev-only tool on a remote run would otherwise report the
        # wrong one. Nothing can start a routine from a phone in phase 2 — the
        # remote floor is EMPTY, so no tool can be named at all — and the check is
        # here anyway, because the two dispatch paths have to agree before there is
        # something for them to disagree about.
        #
        # `not_callable` is the audit outcome, the vocabulary's value for "the
        # request named something that could not run, and nothing about it was
        # examined, approved or reached" — the same call the live_only refusal
        # below makes, for the same reason.
        remote_refusal = self.tool_registry.refuse_if_not_remote(tool_id, surface)
        if remote_refusal is not None:
            return remote_refusal, "not_callable", None
        dev_only_refusal = self.tool_registry.refuse_if_dev_only_outside_open(tool_id, mode)
        if dev_only_refusal is not None:
            return dev_only_refusal, "dev_only", None
        # DISCOVERED BUT NOT WIRED. The live loop's twin, here because a
        # boundary only one dispatch path enforces is not a boundary (SAFE
        # invariant 3's reasoning) — and a routine is the path that runs with
        # nobody watching, so a step naming a tool with no dispatch behind it
        # must refuse here rather than reach a server. Quiet for MCP since phase 3,
        # and still the mechanism the phase constant operates through; the audit
        # row is phase 3's too.
        not_callable_refusal = self.tool_registry.refuse_if_not_callable(tool_id)
        if not_callable_refusal is not None:
            return not_callable_refusal, "not_callable", None
        # NOT FROM A SAVED SPEC (step 8 phase 3, plan §5.10). Switching an
        # automation on or off asks a person to read a preview and retype a
        # short code; a stored, one-click, model-authorable routine that could
        # raise that card mid-run invites answering it on autopilot, which is
        # the exact reflex the code exists to break. Refused HERE rather than at
        # the gate, so the card is never raised at all.
        #
        # SAFE-3 note: this NARROWS what a routine may do relative to live chat,
        # which is the permitted direction — a routine never gains anything, and
        # a person who wants this asks for it in the conversation.
        #
        # `not_callable` is the audit outcome, the vocabulary's value for "the
        # request named something that could not run, and nothing about it was
        # examined, approved or reached" (schema.sql) — true of this word for
        # word. Widening that CHECK to a dedicated value is a migration, and
        # this refusal does not earn one.
        live_only_refusal = self.tool_registry.refuse_if_live_only(tool_id)
        if live_only_refusal is not None:
            return live_only_refusal, "not_callable", None
        # THE HARDLINE DENYLIST (step 5.5, item 3), above the gate: a step
        # naming Addison's own restore storage or the user's credential stores
        # does not run, and is never offered as a card. A routine is persisted,
        # one-click and model-authorable, so this is the site where a forbidden
        # command would otherwise be easiest to smuggle past a person.
        forbidden = self._forbidden_check(tool, resolved_args)
        if forbidden is not None:
            return forbidden, "forbidden", call_permission_detail(tool, resolved_args, affected)
        # CONFINEMENT (step 5, D3): a path-bounded step may only run inside a
        # trusted root. Resolved once by the caller, refused before the gate if
        # outside trust, and handed to execute via the context (R6). The file
        # tools aren't routine-exposed in v1, so this is defence-in-depth;
        # affected_path is None for a command step, which resets resolved_path.
        if affected is not None and not self._trust_check(affected):
            return (
                _OUTSIDE_TRUST,
                "confined_out",
                call_permission_detail(tool, resolved_args, affected),
            )
        return None

    def _record_step(
        self,
        result: ToolResult,
        *,
        step_results: dict[str, ToolResult],
        step_log: list[dict],
        index: int,
        step: RoutineStep,
        run_id: str,
        routine: Routine,
        total: int,
        tool_id: str,
        answer: str,
    ) -> RoutineRunResult | None:
        """THE ONE PLACE ``on_failure`` IS READ (KNOWN-GAPS, closed 2026-08-22).

        Every way a step can end arrives here: the id nothing is registered under,
        each of the five pre-gate guards, and a tool that ran and failed. They used
        to be six copies of abort / ask_user / skip, matching only because each was
        written to look like its neighbours — so a fourth policy would have been
        added to one of them and silently missing from the other five.

        Returns the run's terminal result when the run must STOP, and None to carry
        on with the next step — which is the answer for a success, for "skip", and
        for an ask_user the person answered "keep going" to.
        """
        step_results[step.step_id] = result
        self._step_done(
            step_log, index, step,
            "ok" if result.success else str(result.content),
            run_id=run_id, routine=routine, total=total, tool_id=tool_id, ok=result.success,
        )
        if result.success:
            return None
        message = str(result.content)
        if step.on_failure == "abort":
            return self._finish(run_id, "failed", step_results, message, step_log, answer)
        if step.on_failure == "ask_user" and not self._on_ask_user(step, run_id, message):
            return self._finish(
                run_id, "cancelled", step_results, "Stopped at your request.", step_log, answer,
            )
        return None

    # --- live progress (owner decision 2026-08-12) ---------------------------
    def _step_event(
        self,
        run_id: str,
        routine: Routine,
        index: int,
        total: int,
        step: RoutineStep,
        tool_id: str,
        status: str,
        message: str | None = None,
    ) -> None:
        """One progress event, best-effort — a panel must never be the reason a run
        dies, the same stance ``_audit`` takes about history.

        ``status`` is a closed vocabulary: 'running' (the step has begun), 'ok', and
        'failed'. A step that a person is being asked about is still 'running' here
        — the card itself is what says somebody is waiting, and the surface showing
        both already knows it raised one."""
        try:
            self.on_step(
                {
                    "run_id": run_id,
                    "routine_id": routine.id,
                    "step_id": step.step_id,
                    "index": index,
                    "total": total,
                    "tool_id": tool_id,
                    "status": status,
                    "message": message,
                }
            )
        except Exception:
            pass

    def _step_done(
        self,
        step_log: list[dict],
        index: int,
        step: RoutineStep,
        summary: str,
        *,
        run_id: str,
        routine: Routine,
        total: int,
        tool_id: str,
        ok: bool,
        message: str | None = None,
    ) -> None:
        """A step's outcome, recorded in the run log AND announced, in one call.

        The two used to be one call because only the log existed; they are one call
        still because a run log that says a step failed while the panel says it
        finished is the disagreement this surface exists to prevent. ``message`` is
        what a PERSON reads (the log's own summary is for the log — "ok",
        "permission denied"), defaulting to the summary where they are the same
        sentence."""
        step_log.append(self._log_entry(index, step, summary))
        self._step_event(
            run_id,
            routine,
            index,
            total,
            step,
            tool_id,
            "ok" if ok else "failed",
            None if ok else (message or summary),
        )

    # --- run log (§6.4: backs "show what you just did" for Routine runs) -----
    def _log_entry(self, index: int, step: RoutineStep, summary: str) -> dict:
        return {"step_index": index, "tool_id": step.tool_id, "result_summary": summary}

    def _log_run_started(self, run_id: str, routine_id: str) -> None:
        if self._store is not None:
            self._store.insert_routine_run(
                id=run_id, routine_id=routine_id, started_at=int(time.time())
            )

    def _finish(
        self, run_id, status, step_results, detail, step_log, answer: str = ""
    ) -> RoutineRunResult:
        if self._store is not None:
            self._store.finish_routine_run(
                id=run_id,
                status=status,
                completed_at=int(time.time()),
                step_log=step_log,
            )
        return RoutineRunResult(run_id, status, step_results, detail, answer)