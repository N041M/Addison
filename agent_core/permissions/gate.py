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

from agent_core.policy import PolicyMode


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
    ) -> PermissionStatus:
        """The single mode-aware entry every tool call passes through.

        SAFE mode: identical to the historical ``check()`` -> ``request()`` flow —
        ``destructive`` is ignored, so SAFE prompts for every not-yet-granted tool
        exactly as before. OPEN mode: a non-destructive call auto-grants (logged);
        a destructive call prompts PER INVOCATION — no prior grant is consulted and
        no grant is recorded, so approving one destructive command never authorizes
        another (or a later repeat of the same one). ``detail`` carries what exactly
        is being approved (the command text for run_command) onto the card. A
        denial is remembered for the rest of the turn (cleared by clear_denials at
        the next user message), so a model retry can't nag. The gate runs on EVERY
        call in both modes."""
        if mode is PolicyMode.OPEN:
            if not destructive:
                self.auto_grants.append(tool_id)
                if self._on_auto_grant is not None:
                    self._on_auto_grant(tool_id)
                return PermissionStatus.GRANTED
            return self._request_per_invocation(tool_id, detail)
        status = self.check(tool_id)
        if status == PermissionStatus.NOT_YET_ASKED:
            status = self.request(tool_id)
        return status

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

    def clear_denials(self) -> None:
        """Forget "Not now" answers. The orchestrator calls this at the start of
        every user turn: a denial silences re-asking only for the REST of the
        turn it happened in (so a model retry can't nag), never future turns —
        "Not now" means not now, not never (2026-07 manual pass finding: one
        denial silently blocked the tool for the whole session)."""
        self._denied.clear()
