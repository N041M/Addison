"""Tool protocol, risk tiers, and the undo contract.

Engineering-spec §3, §4.2. A tool whose risk tier is not LOW MUST implement a
real ``undo()`` — this is enforced at registration time in ``registry.py`` and
is the mechanical backbone of the whole safety model (design-doc §7.9).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from agent_core.policy import (
    DENIED_CONTAINS,
    PolicyMode,
    _derived_data_dir,
    command_denied_path,
)


class RiskTier(str, Enum):
    LOW = "low"        # read-only, no undo needed
    MEDIUM = "medium"  # mutating, must have undo()
    HIGH = "high"      # SAFE mode: not permitted at all. OPEN mode: dev-only tools
                       # (e.g. run_command) register at HIGH via ToolRegistry's
                       # allow_missing_undo path and are never in the SAFE view.


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

    # --- workspace-trust file surface (step 5, OPEN-only) ------------------
    # A DELIBERATE departure from the picker-scoped methods above (design-doc §9,
    # R7): the OPEN-mode coding harness edits files by absolute PATH inside a
    # TRUSTED ROOT, not through a per-file picker handle. ``save_new_file`` refuses
    # to overwrite (that refusal is what makes its undo trivial) and
    # ``read_scoped_file`` is handle-based — neither fits an editor, so these three
    # are genuinely new surface. The core confines which paths reach here (the
    # caller's trusted-root check, D3); the shell independently refuses Addison's
    # own data directory (defence in depth, §1.3), and ledgers what it wrote so undo
    # can never restore a path this tool did not create.

    def write_workspace_file(self, path: str, content: str) -> dict:
        """Create-or-OVERWRITE ``path`` with ``content`` (an editor needs both),
        capturing the prior state ATOMICALLY so undo is exact. Returns
        ``{"existed": bool, "prior": str | None}`` — ``prior`` is the previous text
        content (None when the write created the file). REFUSES, writing nothing,
        when the existing file is not valid UTF-8 text or its prior content exceeds
        the undo size bound (so undo can always round-trip it), and refuses any path
        under Addison's own data directory. The path is recorded so a later
        ``restore_workspace_file`` may target it."""
        ...

    def read_workspace_file(self, path: str) -> str:
        """Return the text at ``path`` — used by ``read_project_file``. Refuses a
        binary/oversize file and any path under Addison's own data directory (the
        prior-bytes snapshot for a write is captured by ``write_workspace_file``
        itself, atomically, not through this method)."""
        ...

    def pick_directory(self) -> str:
        """Open the native folder picker and return the chosen absolute directory
        path; raises if the user cancels. Relayed by ``workspace.pickDirectory`` so
        the frontend's "Trust a folder" flow reaches a real OS dialog (§1.3)."""
        ...

    def run_command(self, command: str, timeout_ms: int, write_roots: list[str]) -> dict:
        """Run ``command`` and return
        ``{"stdout": str, "stderr": str, "exitCode": int, "sandboxed": bool}``.

        Step 5.5 item 1 — the LAST OS effect that did not cross this boundary.
        ``run_command`` used to call ``subprocess.run(shell=True)`` from the Agent
        Core, which contradicted engineering-spec §1.3 (*"the Agent Core has no OS
        permissions of its own"*) and, more practically, put execution in the one
        process where no sandbox could be applied. Moving it fixes both with one
        change.

        ``write_roots`` is the live workspace-trust list. The shell turns it into
        the seatbelt profile's write allowlist and **independently re-denies
        Addison's own data directory on top of it**, exactly as the workspace file
        methods do — the core's list is an input to the boundary, never the
        boundary itself.

        ``sandboxed`` says whether a profile was actually applied. It is in the
        return value rather than assumed because **a silent unsandboxed fallback
        is the failure mode this whole step exists to design against**: a guard
        that reports success while doing nothing is this project's own
        anti-pattern. On macOS the shell REFUSES rather than running unconfined;
        elsewhere it runs and answers False, and the tool says so in its output."""
        ...

    def restore_workspace_file(self, path: str, prior_content: str | None) -> None:
        """Undo-time restore for ``write_project_file``: put ``prior_content`` back at
        ``path`` (the bytes it overwrote), or DELETE the file when ``prior_content``
        is None (the write created it). Like ``restore_file`` it only ever touches a
        path THIS session's writes ledgered, so undo can never write or delete an
        arbitrary path — and it works even if the workspace's trust was revoked
        between the write and the undo."""
        ...


@dataclass
class ExecutionContext:
    """Handed to every ``Tool.execute``. Gives tools their only route to
    OS-level effects — always back through the Rust shell via IPC, never a
    raw syscall from the Agent Core (engineering-spec §1.3)."""

    conversation_id: str
    # IPC handle to the Tauri shell — the ShellBridge above; None in CLI/test mode.
    shell_bridge: ShellBridge | None = None
    # The active policy mode for this execution (policy.py). SAFE by default so a
    # tool constructed without a mode behaves conservatively; the orchestrator and
    # routine engine set the live mode. A dev-only tool (run_command) reads this as
    # a belt-and-suspenders check — it refuses to run under SAFE even though the
    # SAFE registry view never surfaces it in the first place.
    policy_mode: PolicyMode = PolicyMode.SAFE
    # The path a path-bounded tool's ``affected_path`` resolved for THIS call (step
    # 5, D4). The caller sets it right before ``execute`` so the tool acts on the
    # exact resolved path the caller checked for confinement — never a re-read of
    # ``args["path"]`` (resolving twice could act on a different path than the one
    # confinement approved: a TOCTOU gap, R6). None for every tool without an
    # ``affected_path`` (run_command, the SAFE tools), and reset per call so a path
    # tool can never inherit a stale value from an earlier call in the same turn.
    resolved_path: str | None = None
    # The live workspace-trust roots, as a CALLABLE (step 5.5, item 2). Only
    # ``run_command`` reads it, to hand the shell the write-allowlist its seatbelt
    # profile is built from.
    #
    # A callable rather than a list, deliberately: a list captured when the context
    # was built is a snapshot of trust taken at the START of the turn, and the one
    # direction a stale trust list can be wrong in is the dangerous one — a root
    # revoked mid-turn would stay writable for the rest of it. Resolving at execute
    # time makes revocation take effect on the very next command. None (CLI/tests)
    # means no roots, which the shell reads as "nothing outside temp is writable".
    trusted_roots: Any = None


@runtime_checkable
class Tool(Protocol):
    """The registration contract: ``definition`` + ``execute``.

    ``undo()`` is deliberately NOT part of this Protocol even though every
    MEDIUM+ tool must ship one — LOW read-only tools (calculator) and dev-only
    registrations legitimately have none, so requiring it structurally would
    misdescribe them. The mandatory-undo invariant lives where it is enforced:
    ``ToolRegistry.register()`` raises for a non-LOW tool without a real
    ``undo``, and ``UndoableTool`` below is the narrowed type for code that
    actually calls ``undo()``."""

    definition: ToolDefinition

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult: ...


@runtime_checkable
class UndoableTool(Tool, Protocol):
    """A ``Tool`` with a real ``undo`` — mandatory for risk_tier MEDIUM+.

    Registration enforces membership for every non-LOW, non-dev tool, so any
    snapshot the UndoManager replays was recorded by a tool that matches this
    Protocol; ``runtime_checkable`` lets it narrow with one ``isinstance``."""

    def undo(self, snapshot: ActionSnapshot) -> None:
        """Reverse the action captured in ``snapshot``. A tool that cannot
        implement this MUST declare risk_tier=LOW and MUST NOT mutate state."""
        ...


@runtime_checkable
class RedoableTool(Protocol):
    """A ``Tool`` that also supports re-applying an undone action (§4.5 redo).

    ``redo()`` is OPT-IN, never mandatory — a tool without it can still be undone,
    it simply can't be re-done (the UndoManager reports that in plain language).
    Adding redo() never weakens the mandatory-undo invariant. This Protocol is
    ``runtime_checkable`` so ``UndoManager`` can discover redo support with a single
    ``isinstance(tool, RedoableTool)`` rather than duck-typing ``getattr``; because
    it lists execute/undo/redo, only a genuine tool that implements all three
    matches (SaveFileTool does)."""

    definition: ToolDefinition

    def execute(self, args: dict, context: ExecutionContext) -> ToolResult: ...

    def undo(self, snapshot: ActionSnapshot) -> None: ...

    def redo(self, snapshot: ActionSnapshot) -> None:
        """Re-apply an action that ``undo()`` reversed. Required to satisfy this
        Protocol; a tool that can't offer it simply isn't a ``RedoableTool``."""
        ...


def call_is_destructive(tool: Any, args: dict) -> bool:
    """Per-call destructiveness for the mode-aware PermissionGate (OPEN mode).

    In OPEN mode the gate auto-allows a non-destructive call and prompts for a
    destructive one (policy.py, spec §8 SAFE-mode carve-out). A tool may classify
    its OWN call by implementing ``is_destructive(args) -> bool``. Two tools do:
    ``run_command`` returns True UNCONDITIONALLY — every command cards, because
    statically deciding whether an arbitrary shell command is read-only is a losing
    game (the read-only allowlist was defeated three ways and removed, #48; see its
    docstring) — and ``write_project_file`` returns True because an overwrite is
    data loss, so a card is the belt behind confinement (step 5, R2). With no
    classifier, a call is destructive iff the tool's tier is HIGH; LOW and MEDIUM
    tools are non-destructive. (SAFE mode ignores this entirely — it prompts for
    every not-yet-granted tool as before.)"""
    classifier = getattr(tool, "is_destructive", None)
    if callable(classifier):
        return bool(classifier(args))
    return tool.definition.risk_tier is RiskTier.HIGH


# The answer when a path-bounded tool cannot work out WHICH path it was given —
# a missing/non-string argument, or one the OS refuses to resolve at all (an
# embedded NUL makes ``Path.resolve()`` raise ValueError). It is a string, so it
# flows through the caller's ordinary confinement check unchanged; it contains a
# NUL, so it can never name a real file and can never sit inside a trusted root,
# and the check refuses it the same way it refuses ``/etc/passwd``.
#
# NOT ``None``: None means "this is not a path-bounded tool at all", which SKIPS
# confinement entirely — that is ``run_command``'s honest answer and must keep
# working. Collapsing an unresolvable path onto it instead would have let a
# malformed ``path`` argument walk straight past the boundary and into the gate,
# where, being destructive, it raised a card the user could approve. And before
# that, the unguarded ``resolve()`` took the whole turn down with it: a routine run
# was left recorded as ``running`` forever, which is the exact failure the engine's
# own error handling exists to prevent.
UNRESOLVABLE_PATH = "\x00unresolvable"


# What the person (and the model) is told when the denylist refuses a call. Plain
# language, and both say the thing that matters first: nothing ran. Neither is
# phrased as a permission problem — there is no card behind it to approve.
#
# TWO messages, because the two directions are not equally recoverable. A CONTAINS
# refusal has an obvious next move and the sentence hands it over, so the model
# corrects in one turn instead of reporting the whole task as blocked. One shared
# message made every ``ls ~`` look like a dead end.
FORBIDDEN_CALL_INSIDE = (
    "That reaches into a folder Addison never lets a command touch — its own "
    "restore points, or where your keys and passwords are kept. Nothing was run."
)
FORBIDDEN_CALL_CONTAINS = (
    "That names a folder that holds Addison's own restore points, so Addison "
    "won't run a command across the whole of it. Nothing was run. Name the "
    "folder inside it that you actually mean."
)


def default_forbidden_check(tool: Any, args: dict) -> str | None:
    """The store-free fallback used when no ``forbidden_check`` was wired.

    Mirrors ``WorkspaceMixin._data_dir``'s own fallback exactly: the live server
    always injects a check bound to the RUNNING store's directory, and this only
    covers the constructed-without-a-server case (tests). It is the one place in
    the tree allowed to re-derive the data dir for this predicate — a source test
    (`test_step_5_5_containment`) pins that.

    ``_derived_data_dir`` is imported at MODULE level with the rest of policy.
    It sat inside this body with no reason given, which reads as "there is a
    cycle here" — there is not: policy imports only ``profiles``, and this module
    already imports three other names from it at the top. It is also not a
    deferral of any work, because the function reads ``ADDISON_DB_PATH`` when
    CALLED, so binding the name early changes nothing about when the answer is
    computed."""
    return call_is_forbidden(tool, args, _derived_data_dir())


def call_is_forbidden(tool: Any, args: dict, data_dir: str) -> str | None:
    """A refusal sentence, or None. Checked BEFORE the gate: this is not a card the
    person can approve, it is a call that does not happen.

    The hardline denylist (step 5.5 item 3). It sits at the same three dispatch
    sites as the confinement check — the live loop, the routine engine, and the
    command widget's Run pill — because a boundary that only one of them enforces
    is not a boundary (SAFE invariant 3's reasoning, applied to containment).

    ONE source, and it is not a parser: a tool that declares the raw text it would
    hand to a shell, via an optional ``command_text(args) -> str | None``.
    ``run_command`` is the only one today, and it is the reason this function
    exists — it has no ``affected_path``, so confinement never governs it and the
    card is the only layer underneath.

    A PATH-BOUNDED TOOL IS DELIBERATELY NOT CHECKED HERE. Confinement already
    refuses those at these same three sites, and it does so against the LIVE data
    directory, which the caller holds and this function does not. Re-deriving the
    data dir here to run a second copy of the floor produced a check that was both
    redundant and less informed than the one beside it — two spellings of one
    boundary, which is precisely what CLAUDE.md's one-owner rule exists to stop.

    **Scope the guarantee honestly** — ``policy.command_denied_path`` explains why
    at length. Pattern-matching a ``shell=True`` string is a backstop against the
    obvious, defeated by quoting, and it must never be described as the boundary.
    The boundary is the seatbelt profile (item 2).

    ``data_dir`` is the LIVE data directory, passed in by the caller — never
    re-derived here. See ``policy.denylisted_roots`` for why that is a signature
    and not a convention."""
    provider = getattr(tool, "command_text", None)
    if callable(provider):
        command = provider(args)
        if command:
            denial = command_denied_path(str(command), data_dir)
            if denial is not None:
                _, direction = denial
                return (
                    FORBIDDEN_CALL_CONTAINS
                    if direction == DENIED_CONTAINS
                    else FORBIDDEN_CALL_INSIDE
                )
    return None


def call_affected_path(tool: Any, args: dict) -> str | None:
    """The absolute filesystem path this call would touch, RESOLVED ONCE, or None.

    A path-bounded tool (``read_project_file``, ``write_project_file``) implements
    ``affected_path(args) -> str | None`` and returns ``Path(args["path"]).resolve()``
    — realpath'd exactly once here so the value the CALLER checks for confinement
    (rpc/workspace.is_trusted) is the very value ``execute`` then acts on (handed
    over via ``ExecutionContext.resolved_path``). Resolving again inside ``execute``
    would reopen a TOCTOU gap: confinement could approve one path while the write
    lands on another (R6, D4). Every other tool (run_command included) has no
    ``affected_path`` and returns None, so confinement never applies to it — which
    is exactly why run_command's cwd is a convenience, never an effect bound, and it
    is never trust-suppressed (contract §4).

    Never raises. A tool that declares ``affected_path`` is path-bounded, so if it
    cannot produce a usable path this returns ``UNRESOLVABLE_PATH`` — a value the
    caller's trust check refuses — rather than ``None``, which would mean "not a
    path tool" and skip the boundary altogether."""
    provider = getattr(tool, "affected_path", None)
    if not callable(provider):
        return None
    try:
        value = provider(args)
    except (OSError, ValueError, TypeError):
        # ``Path(...).resolve()`` raises ValueError on an embedded NUL and OSError
        # on some malformed paths. A model can put either in a tool call, and this
        # runs OUTSIDE the orchestrator's per-call error handling, so letting it
        # escape ends the turn rather than the step.
        return UNRESOLVABLE_PATH
    # None is passed through UNCHANGED: for ``run_command`` it means "no path bound
    # at all", and turning that into the sentinel would confine — and refuse — every
    # command. A PATH tool that cannot read its own argument returns the sentinel
    # itself (read_project_file / write_project_file), which is the case that must
    # not reach the gate.
    return str(value) if value is not None else None


# The User-Agent every outbound tool request carries. One string, because two
# copies drift and the day they do, one tool starts getting a different page than
# the other for reasons nobody will connect to a header. A plain desktop browser
# UA: several sites serve a stripped-down layout to anything else, and a page with
# its text stripped out is a page this app cannot answer from.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# How much of a permission detail may be shown, and the shape of the cut. Capped
# HERE, at the one place a detail is constructed, rather than at each surface that
# renders one: the card and the Activity Panel are two renderings of a single value,
# and two independent truncations could describe the same call differently.
#
# There is a cap at all because a detail is attacker-influenced by construction —
# read_web_page's is the host out of a URL the model chose, and that URL normally
# arrived FROM a web page — so an uncapped string could push the rest of the work
# list off the screen, which defeats the visibility the field exists to provide.
MAX_PERMISSION_DETAIL_CHARS = 120


def call_permission_detail(tool: Any, args: dict) -> str | None:
    """What exactly this call would do, in words the person will see.

    A tool may implement ``permission_detail(args) -> str``; None (the default)
    leaves the surface with just the tool's static label/description.

    TWO CONSUMERS, and the second one is the reason this needs reading carefully:

      * the per-invocation permission card for destructive OPEN-mode actions
        (gate.authorize), which shows what is being approved each time —
        ``run_command`` returns the command text here;
      * the Activity Panel, on EVERY granted call in BOTH modes
        (orchestrator -> ``main._emit_activity`` -> ``tool.activityUpdate``).

    So the contract is not "text for a rare confirmation dialog". **A detail is
    user-visible on every call, and it leaves the Agent Core for the webview — the
    lowest-trust process.** It must therefore never contain a secret, a filesystem
    path, or a full URL: ``read_web_page`` deliberately returns the HOST only,
    because a query string can carry whatever a page hid in it and would land in
    the panel, and in any screenshot of it. Return the least that still tells the
    person what is being touched."""
    provider = getattr(tool, "permission_detail", None)
    if callable(provider):
        value = provider(args)
        if not value:
            return None
        text = str(value)
        if len(text) > MAX_PERMISSION_DETAIL_CHARS:
            text = text[:MAX_PERMISSION_DETAIL_CHARS] + "…"
        return text
    return None
