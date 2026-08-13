"""Permission Gate — engineering-spec §4.3, mode-aware (owner decision 2026-07-19).

The orchestrator (and routine engine) call ``authorize()`` before EVERY tool
execution, not just the first time, so a permission revoked in Settings takes
effect immediately AND the gate runs on every call in both modes.

SAFE mode (Simple profile): a not-yet-granted tool prompts, and consent is then
remembered per tool id (the coarse grant state) — byte-for-byte the historical
behaviour for everything non-destructive. Since 2026-08-11 a DESTRUCTIVE call in
SAFE takes the per-invocation card instead, which is design-doc §7.4's
"re-confirm per distinct action" finally enforced HERE rather than promised to the
orchestrator/frontend. It arrived with the one SAFE tool that is destructive —
``write_project_file``, which Simple gained the same day (docs/SAFETY.md owns the
decision) — and it is a tightening: nothing that used to card stops carding.

OPEN mode (Developer profile) is "open" = fewer prompts, NOT no gate: a
non-destructive call auto-grants (recorded so the UI can still show what
happened); a destructive call raises a permission card PER INVOCATION — a prior
grant never carries over, so approving one destructive command can never silently
authorize a different (or even the same) one later. The card carries the actual
command text so the user knows exactly what they are approving each time.
Destructiveness is decided per call by the caller
(``tools.base.call_is_destructive``) and passed in.

THE ARMING PATH (step 8 phase 3, GLOBAL FLOOR G2) is the one card this gate does
not let any setting soften. When ``authorize`` is handed an ``arming`` preview it
takes ``_request_arming`` and nothing else: no auto-grant, no session grant, no
trust suppression, no guard. The card carries a short code the person retypes
(``agent_core/automation_nonce.py``), which the CALLER mints, holds and compares —
this module never sees a nonce, and neither does any tool. See
``docs/step-8-automation-plan.md`` §3 and §5.9 (the nonce ships non-tunable).
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from agent_core.policy import GuardConfig, PolicyMode


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_YET_ASKED = "not_yet_asked"


@dataclass
class PermissionRequest:
    tool_id: str
    status: PermissionStatus


def call_arming_refusal(tool: object, args: dict) -> str | None:
    """The arming DOOR, asked at the dispatch site BEFORE this gate (step 8 phase 3).

    A tool may implement ``arming_refusal(args) -> str | None``; anything that does
    not (every tool but the two arming ones) answers None and is unaffected.

    It lives beside the gate rather than in ``tools/base.py`` because it is the one
    check whose entire purpose is *what must be true before a card may be raised*.
    A refused call is not a card anybody may approve — the same rule the hardline
    denylist follows one line above it at every dispatch site — and here it is the
    difference between "type this code to switch on Nightly backup" and asking
    somebody to perform a ceremony for a row that no longer exists, a schedule
    nothing can run, or a computer with no launchd on it.

    Never raises: a tool whose door itself fails is refused, not escalated."""
    provider = getattr(tool, "arming_refusal", None)
    if not callable(provider):
        return None
    try:
        value = provider(args)
    except Exception:
        return None
    return str(value) if value else None


def tool_requires_arming(tool: object) -> bool:
    """Does this tool's consent REQUIRE the keyword ceremony? Asked of the tool, not
    of the payload — which is the whole point.

    ``arm_automation`` declares ``arming_card``; nothing else does. Declaring it is
    the tool saying *"an ordinary card is not enough consent for me"*, and that is a
    property of the TOOL, permanent and independent of whether a particular preview
    could be assembled.

    THE FAIL-OPEN THIS CLOSES (adversarial review, 2026-08-07). The gate used to
    take the arming path iff a preview ARRIVED, so a preview that could not be built
    — ``arming_card`` returns None whenever the row cannot be read, and ``_row``
    swallows every store error — silently downgraded the call to an ordinary
    destructive card. Under the Custom profile's ``auto_grant_scope='everything'``
    it downgraded to NO CARD AT ALL, which is verbatim the failure ``authorize``'s
    own docstring says the arming branch's ordering exists to prevent. A transient
    SQLite error was enough. **A door that fails must not be a door that opens**, so
    the requirement now lives on the tool and a missing preview is a refusal."""
    return callable(getattr(tool, "arming_card", None))


def call_arming_card(tool: object, args: dict) -> dict | None:
    """The arming PREVIEW this call would put on its card, or None.

    A tool may implement ``arming_card(args) -> dict | None`` returning the fields
    ``PermissionRequest.arming`` declares minus the two the caller owns: the name,
    the schedule in plain words, the exact command, where the file goes, and the
    frozen warnings. Returning None — which is what every tool but ``arm_automation``
    does, ``disarm_automation`` INCLUDED — means an ordinary card.

    That distinction is the whole of §5.10's "no ceremony on a tightening": switching
    something OFF must never be the thing a code traps somebody out of.

    Never raises, and a non-dict answer is treated as no preview: a card that
    silently lost its preview would be a code with nothing above it to read, which
    is the ceremony reduced to a captcha."""
    provider = getattr(tool, "arming_card", None)
    if not callable(provider):
        return None
    try:
        value = provider(args)
    except Exception:
        return None
    return value if isinstance(value, dict) and value else None


def _handler_takes_preview(handler: object) -> bool:
    """Can this permission handler be told about the delete preview (5.6)?

    Asked of the SIGNATURE rather than discovered by calling and catching a
    ``TypeError``: a handler that raised one from somewhere inside itself would be
    called a second time, and a permission card is not a thing to emit twice.
    A handler this cannot read is treated as one that cannot take it, which is the
    silent-and-unchanged direction."""
    try:
        parameters = inspect.signature(handler).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    for parameter in parameters.values():
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if parameter.name == "preview" and parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


class PermissionGate:
    def __init__(
        self,
        on_request: Callable[..., PermissionStatus] | None = None,
        on_auto_grant: Callable[[str], None] | None = None,
    ) -> None:
        # on_request emits the IPC event the frontend renders as a PermissionCard
        # and blocks until the user answers. In CLI/test mode a stub can auto-answer.
        # Called as on_request(tool_id) on the SAFE path; the destructive-in-OPEN
        # per-invocation path calls on_request(tool_id, detail) so the card can show
        # exactly what is being approved — a real handler accepts (tool_id, detail=None).
        # The ARMING path (step 8 phase 3) calls on_request(tool_id, detail, arming)
        # with the keyword card's preview; a handler that supports it accepts
        # (tool_id, detail=None, arming=None). The third argument is passed ONLY on
        # that path, so every existing two-argument handler is untouched.
        self._on_request = on_request
        # on_auto_grant fires when OPEN mode auto-allows a non-destructive call, so
        # the server can surface it in the activity log. None in CLI/tests.
        self._on_auto_grant = on_auto_grant
        self._grants: dict[str, dict] = {}   # tool_id -> {granted_at, scope_details}
        self._denied: set[str] = set()
        # Custom-profile "Ask once" (destructive_card='session', D2): a destructive
        # tool approved once is remembered here for the rest of the session. It is a
        # DEDICATED set, never ``_grants`` [R2] — the SAFE ``check()`` path consults
        # only ``_grants``, so a session-destructive grant can NEVER leak into SAFE
        # mode by construction, not merely because ``revoke_all`` runs on the profile
        # switch. In-memory, per-session; never persisted.
        self._destructive_session_grants: set[str] = set()
        # OPEN-mode auto-grants recorded this session (the "activity log" the UI can
        # show as auto-approved). In-memory, per-session; never persisted.
        self.auto_grants: list[str] = []

    def authorize(
        self,
        tool_id: str,
        *,
        mode: PolicyMode = PolicyMode.SAFE,
        destructive: bool = False,
        detail: str | None = None,
        guards: GuardConfig | None = None,
        trusted: bool = False,
        arming: dict | None = None,
        requires_arming: bool = False,
        preview: str | None = None,
    ) -> PermissionStatus:
        """The single mode-aware entry every tool call passes through.

        SAFE mode: the historical ``check()`` -> ``request()`` flow for every
        non-destructive call — SAFE prompts for every not-yet-granted tool exactly
        as before, and ``guards`` are ignored. A DESTRUCTIVE call takes the
        per-invocation card instead (2026-08-11; see the branch's own comment):
        asked every time, carrying ``detail``, never remembered. OPEN mode: a non-destructive call
        auto-grants (logged); a destructive call prompts PER INVOCATION — no prior
        grant is consulted and no grant is recorded, so approving one destructive
        command never authorizes another (or a later repeat of the same one).
        ``detail`` carries what exactly is being approved (the command text for
        run_command) onto the card. A denial is remembered for the rest of the turn
        (cleared by clear_denials at the next user message), so a model retry can't
        nag. The gate runs on EVERY call in both modes.

        ``guards`` (Custom profile, D2/D3) only MODULATES the OPEN path. ``None`` ≡
        the fixed defaults ≡ today's OPEN behaviour byte-for-byte — that equivalence
        is the freeze. The two guards:
          * ``auto_grant_scope`` governs NON-DESTRUCTIVE calls (with one explicit
            exception): 'everything' -> every call auto-grants, destructive included
            (still logged; the one place scope overrides the card guard). = 'none'
            -> non-destructive calls run the SAFE-style coarse check/request flow
            (asks about everyday actions too). = 'non_destructive' (default) ->
            non-destructive auto-grants.
          * ``destructive_card`` governs DESTRUCTIVE calls under every scope except
            'everything' = 'per_invocation' (default) -> card every time, no grant
            kept. = 'session' -> first destructive call of a tool cards, then it is
            remembered for the session in ``_destructive_session_grants`` (never
            ``_grants`` — [R2]).

        Destructive NEVER falls into the coarse ``_safe_flow`` under any scope
        (adversarial pass, 2026-07-24): the coarse flow remembers a grant per tool
        id with no per-call text, so routing destructive through it would let one
        approved ``ls`` silently authorize every later ``rm -rf`` — precisely under
        the scope LABELLED "ask about everything", and counted as a tightening, so
        no anchor would ever have been minted. The two knobs stay orthogonal
        instead: scope decides how often everyday actions ask, the card guard alone
        decides how destructive ones do.

        ``trusted`` (step 5, D3) is workspace-trust: True ONLY for a typed,
        path-bounded, undoable file tool whose RESOLVED path the caller confirmed
        sits inside a trusted root (the caller resolves it store-side; the gate stays
        store-free — F6). A trusted destructive call auto-grants card-free (recorded
        + logged) — that IS the harness payoff, card-free undoable editing inside a
        trusted project (§8.3). It is a permission-to-SKIP-the-card only: the caller
        has already confined WHICH path may be touched (permission-to-touch, D3), so
        the two are separate gates. ``run_command`` is never trusted (its
        ``affected_path`` is None, so the caller cannot mark it) and neither are
        stored/replayable surfaces — routine command steps and command widgets pass
        ``trusted=False`` unconditionally (D5), so a persisted one-click spec can
        never skip a card. ``trusted=False`` ≡ today byte-for-byte (the freeze), and
        SAFE ignores ``trusted`` entirely (F7).

        ``arming`` (step 8 phase 3) is the preview for the keyword card. When it is
        not None this method does ONE thing — ``_request_arming`` — and every branch
        below is skipped, in every mode. That ordering is the floor rather than a
        convenience, and each thing it steps over is a real way the ceremony would
        otherwise have been lost:

          * ``auto_grant_scope='everything'`` would have armed a recurring,
            unconfined job with NO CARD AT ALL, because that scope auto-grants
            destructive calls too;
          * ``destructive_card='session'`` would have armed the SECOND automation of
            a session on the strength of a code typed for the FIRST;
          * ``trusted`` skips the card for a confined edit. Arming has no
            ``affected_path``, so no caller can set it — stepping over it anyway
            costs nothing and removes the question;
          * SAFE would have run the coarse remembered-grant flow. Dispatch already
            refuses these tools outside OPEN and that is the enforcement; this is
            the belt, and a belt on this particular floor is cheap.

        ``requires_arming`` is asked of the TOOL (``tool_requires_arming``) and is
        what makes the ceremony non-optional: when it is true and no preview
        arrived, this DENIES rather than falling through to an ordinary card. The
        preview can only go missing because the row could not be read, and a call
        that cannot describe what it would arm is not a call anybody can consent to.
        See ``tool_requires_arming`` for the fail-open this closes.

        ``preview`` (5.6, the delete preview) is ONE extra plain line for the card
        and nothing else: "About to delete 1,240 files in 12 folders." It rides
        with the per-invocation and session cards only, it never reaches a grant,
        an auto-grant, an audit row or the Activity Panel, and it changes no
        decision this method makes. The caller builds it (``delete_preview.py``)
        and answers None whenever it cannot be sure, absence is the shipped state,
        so a handler that does not accept it simply shows the card it always did.

        ``arming=None`` with ``requires_arming=False`` and ``preview=None`` ≡ every
        previous call byte-for-byte, which is the freeze."""
        if requires_arming and arming is None:
            # Refused, never downgraded. Nothing is recorded: there is no grant to
            # remember and no denial to nag about — the call simply could not be
            # put in front of anybody.
            return PermissionStatus.DENIED
        if arming is not None:
            return self._request_arming(tool_id, detail, arming)
        if mode is PolicyMode.OPEN:
            effective = guards if guards is not None else GuardConfig()
            if effective.auto_grant_scope == "everything":
                # Fewer prompts, not no gate: destructive auto-grants too, but the
                # grant is still recorded and announced ("Never ask" is a choice the
                # Activity Panel still shows).
                return self._auto_grant(tool_id)
            if not destructive:
                if effective.auto_grant_scope == "none":
                    # "Ask about everything": everyday actions run the SAFE-style
                    # coarse flow instead of auto-granting.
                    return self._safe_flow(tool_id)
                return self._auto_grant(tool_id)
            if trusted and effective.auto_grant_scope != "none" and tool_id not in self._denied:
                # A confined, undoable file edit inside a trusted workspace: no card,
                # still recorded + logged so the Activity Panel shows it happened.
                # The caller only sets this for a path INSIDE trust, so it can never
                # widen WHICH path is touched — only whether the card is shown.
                #
                # TWO THINGS TRUST DOES NOT OVERRIDE, both found by the post-build
                # adversarial pass:
                #  * A turn-scoped "Not now". The don't-nag rule cuts both ways: a
                #    denial is honoured for the rest of the turn, and a person who
                #    was shown a card and said no must not then watch Addison edit a
                #    file in the same turn. Nothing is escalated either way (the call
                #    was card-free anyway) — what was broken is consent HONESTY.
                #  * Custom's ``auto_grant_scope='none'``, the strictest option the
                #    panel offers, whose copy says Addison asks about everything.
                #    Trust silently making destructive writes card-free under that
                #    setting is the same defect shape the step-2 rigor pass found:
                #    the strictest-LABELLED option carrying the quiet hole, and a
                #    tightening minting no anchor, so nothing marks the moment.
                #    Simple/Developer are untouched — their guards are the defaults,
                #    where this branch behaves exactly as step 5 built it.
                return self._auto_grant(tool_id)
            if effective.destructive_card == "session":
                return self._request_destructive_session(tool_id, detail, preview)
            return self._request_per_invocation(tool_id, detail, preview)
        if destructive:
            # SAFE + DESTRUCTIVE (2026-08-11, the Simple-can-edit-a-file decision).
            # The coarse flow below remembers a grant per TOOL ID with no per-call
            # text, which is right for "may Addison read files you hand it" and
            # wrong for "may Addison overwrite this file": one Allow would cover
            # every later overwrite in the session, unannounced. So a destructive
            # call in SAFE takes the same per-invocation card OPEN gives it —
            # asked every time, carrying ``detail`` (the file's name), never
            # remembered — which is design-doc §7.4's "re-confirm per distinct
            # action" for the one SAFE tool that is destructive.
            #
            # It is a TIGHTENING of the coarse flow, never a loosening: nothing
            # that used to card stops carding. Today the only SAFE-visible
            # destructive tool is ``write_project_file`` (LOW/MEDIUM tools are
            # non-destructive without their own classifier, and no HIGH tool is in
            # the SAFE view), so the rest of SAFE is byte-for-byte what it was.
            return self._request_per_invocation(tool_id, detail, preview)
        return self._safe_flow(tool_id)

    def _safe_flow(self, tool_id: str) -> PermissionStatus:
        """The historical SAFE check -> request path: prompt once for a not-yet-
        granted tool, then remember the coarse grant. Shared by SAFE mode and by
        the NON-DESTRUCTIVE side of OPEN's ``auto_grant_scope='none'`` guard.
        Destructive calls never come through here in OPEN mode — a coarse grant
        with no per-call text must not cover them (see ``authorize``)."""
        status = self.check(tool_id)
        if status == PermissionStatus.NOT_YET_ASKED:
            status = self.request(tool_id)
        return status

    def _auto_grant(self, tool_id: str) -> PermissionStatus:
        """OPEN-mode auto-allow: granted with no card, recorded both ways so the UI
        can show it happened. Used for non-destructive calls, and for every call
        under the 'everything' scope."""
        self.auto_grants.append(tool_id)
        if self._on_auto_grant is not None:
            self._on_auto_grant(tool_id)
        return PermissionStatus.GRANTED

    def _request_per_invocation(
        self, tool_id: str, detail: str | None, preview: str | None = None
    ) -> PermissionStatus:
        """The destructive card, in BOTH modes since 2026-08-11: asked EVERY time,
        never remembered as a grant. Only the turn-scoped denial is
        honoured/recorded (don't-nag rule)."""
        if tool_id in self._denied:
            return PermissionStatus.DENIED
        if self._on_request is None:
            raise RuntimeError("PermissionGate has no request handler wired (frontend/IPC).")
        status = self._ask(tool_id, detail, preview)
        if status == PermissionStatus.DENIED:
            self._denied.add(tool_id)
        return status

    def _ask(
        self, tool_id: str, detail: str | None, preview: str | None
    ) -> PermissionStatus:
        """Put ONE ordinary card in front of the handler, with the delete preview
        when there is one and the handler can show it (5.6).

        The extra line is passed as a KEYWORD and only when it exists, so every
        handler written against ``on_request(tool_id, detail=None)``, the CLI
        stand-in, every test stub, is called exactly as it was. A handler that
        cannot take it gets the card it always got: the preview is advisory, and
        its absence is the state this app shipped with, so degrading to silence
        costs a line of text and no correctness.

        The caller has already refused a missing handler, so the check below is a
        type-level one rather than a second policy."""
        if self._on_request is None:
            raise RuntimeError("PermissionGate has no request handler wired (frontend/IPC).")
        if preview and _handler_takes_preview(self._on_request):
            return self._on_request(tool_id, detail, preview=preview)
        return self._on_request(tool_id, detail)

    def _request_arming(
        self, tool_id: str, detail: str | None, arming: dict
    ) -> PermissionStatus:
        """The keyword card (step 8 phase 3, G2): ALWAYS asked, never remembered.

        Structurally the per-invocation card with two differences, and both are
        deliberate:

          * the ``arming`` preview rides along to the handler, which is what makes
            this a keyword card rather than a button — the handler mints the code,
            shows it above the preview, and compares what comes back
            (``agent_core/automation_nonce.py``). **This module never sees a
            nonce.** It cannot log one, cannot cache one, and cannot be edited into
            comparing one loosely, because it has none to compare;
          * no guard reaches here at all (see ``authorize``).

        A GRANT IS NEVER RECORDED — not in ``_grants``, not in
        ``_destructive_session_grants``. Arming twice means typing twice, forever.
        A denial is turn-scoped exactly like every other card's: "not now" means not
        now, and it must not become a way to nag somebody into arming.

        The handler is invoked with THREE arguments here and two everywhere else, so
        every existing ``on_request(tool_id, detail=None)`` keeps working untouched
        and only a handler that has opted into arming is ever asked about it. A
        handler that has not is a handler that cannot show the code — and raising a
        plain card in its place would be the ceremony silently downgraded, which is
        the one failure this path may not have, so it surfaces as the wiring error
        it is."""
        if tool_id in self._denied:
            return PermissionStatus.DENIED
        if self._on_request is None:
            raise RuntimeError("PermissionGate has no request handler wired (frontend/IPC).")
        status = self._on_request(tool_id, detail, arming)
        if status == PermissionStatus.DENIED:
            self._denied.add(tool_id)
        return status

    def _request_destructive_session(
        self, tool_id: str, detail: str | None, preview: str | None = None
    ) -> PermissionStatus:
        """Custom "Ask once" (destructive_card='session', D2): the FIRST destructive
        call of a tool cards (carrying its detail); an approval is then remembered
        for the session — but in ``_destructive_session_grants``, NEVER ``_grants``
        [R2]. SAFE ``check()`` reads only ``_grants``, so this remembered approval is
        structurally invisible to SAFE mode and cannot survive a switch to Simple.
        A denial is turn-scoped, exactly like the per-invocation card."""
        if tool_id in self._destructive_session_grants:
            return PermissionStatus.GRANTED
        if tool_id in self._denied:
            return PermissionStatus.DENIED
        if self._on_request is None:
            raise RuntimeError("PermissionGate has no request handler wired (frontend/IPC).")
        status = self._ask(tool_id, detail, preview)
        if status == PermissionStatus.GRANTED:
            self._destructive_session_grants.add(tool_id)
        elif status == PermissionStatus.DENIED:
            self._denied.add(tool_id)
        return status

    def check(self, tool_id: str) -> PermissionStatus:
        if tool_id in self._grants:
            return PermissionStatus.GRANTED
        if tool_id in self._denied:
            return PermissionStatus.DENIED
        return PermissionStatus.NOT_YET_ASKED

    def request(self, tool_id: str) -> PermissionStatus:
        """Surfaces the consent UI and blocks the orchestrator's current step
        until the frontend responds. The model does not see a tool_result for
        this call until the user has answered."""
        if self._on_request is None:
            raise RuntimeError("PermissionGate has no request handler wired (frontend/IPC).")
        status = self._on_request(tool_id)
        if status == PermissionStatus.GRANTED:
            self.grant(tool_id)
        elif status == PermissionStatus.DENIED:
            self._denied.add(tool_id)
        return status

    def grant(self, tool_id: str, scope_details: dict | None = None) -> None:
        self._denied.discard(tool_id)
        self._grants[tool_id] = {
            "granted_at": int(time.time()),
            "scope_details": scope_details,
        }

    def revoke(self, tool_id: str) -> None:
        self._grants.pop(tool_id, None)

    def revoke_all(self) -> None:
        """Forget every grant this session accumulated — both the coarse ``_grants``
        AND the Custom session-destructive grants. Called after a G3 restore AND on
        every profile change (rpc/snapshots.py, rpc/profile.py): grants live only
        here, so leaving them in place would leave the session's permission posture
        WIDER than the config/profile the user just moved to. One extra card next
        time a tool runs is the right price for a recovery or a downgrade."""
        self._grants.clear()
        self._destructive_session_grants.clear()

    def clear_denials(self) -> None:
        """Forget "Not now" answers. The orchestrator calls this at the start of
        every user turn: a denial silences re-asking only for the REST of the
        turn it happened in (so a model retry can't nag), never future turns —
        "Not now" means not now, not never (2026-07 manual pass finding: one
        denial silently blocked the tool for the whole session)."""
        self._denied.clear()
