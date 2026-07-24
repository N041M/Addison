"""Permission Gate — engineering-spec §4.3, mode-aware (owner decision 2026-07-19).

The orchestrator (and routine engine) call ``authorize()`` before EVERY tool
execution, not just the first time, so a permission revoked in Settings takes
effect immediately AND the gate runs on every call in both modes.

SAFE mode (Simple profile) is byte-for-byte the historical behaviour: a
not-yet-granted tool prompts; consent for LOW-risk tools is remembered once
granted; MEDIUM tools re-confirm per distinct action (design-doc §7.4 — that
per-action policy lives in the orchestrator/frontend; this gate tracks the coarse
grant state).

OPEN mode (Developer profile) is "open" = fewer prompts, NOT no gate: a
non-destructive call auto-grants (recorded so the UI can still show what
happened); a destructive call raises a permission card PER INVOCATION — a prior
grant never carries over, so approving one destructive command can never silently
authorize a different (or even the same) one later. The card carries the actual
command text so the user knows exactly what they are approving each time.
Destructiveness is decided per call by the caller
(``tools.base.call_is_destructive``) and passed in.
"""

from __future__ import annotations

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
    ) -> PermissionStatus:
        """The single mode-aware entry every tool call passes through.

        SAFE mode: identical to the historical ``check()`` -> ``request()`` flow —
        ``destructive`` and ``guards`` are ignored, so SAFE prompts for every
        not-yet-granted tool exactly as before. OPEN mode: a non-destructive call
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
        decides how destructive ones do."""
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
            if effective.destructive_card == "session":
                return self._request_destructive_session(tool_id, detail)
            return self._request_per_invocation(tool_id, detail)
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

    def _request_per_invocation(self, tool_id: str, detail: str | None) -> PermissionStatus:
        """The destructive-in-OPEN card: asked EVERY time, never remembered as a
        grant. Only the turn-scoped denial is honoured/recorded (don't-nag rule)."""
        if tool_id in self._denied:
            return PermissionStatus.DENIED
        if self._on_request is None:
            raise RuntimeError("PermissionGate has no request handler wired (frontend/IPC).")
        status = self._on_request(tool_id, detail)
        if status == PermissionStatus.DENIED:
            self._denied.add(tool_id)
        return status

    def _request_destructive_session(
        self, tool_id: str, detail: str | None
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
        status = self._on_request(tool_id, detail)
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
