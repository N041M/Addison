"""Permission Gate — engineering-spec §4.3.

The orchestrator calls ``check()`` before EVERY tool execution, not just the
first time, so a permission revoked in Settings takes effect immediately.

Consent for LOW-risk tools is remembered once granted; MEDIUM tools re-confirm
per distinct action (design-doc §7.4). That per-action policy lives in the
orchestrator/frontend; this gate tracks the coarse grant state.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    NOT_YET_ASKED = "not_yet_asked"


@dataclass
class PermissionRequest:
    tool_id: str
    status: PermissionStatus


class PermissionGate:
    def __init__(self, on_request: Callable[[str], PermissionStatus] | None = None) -> None:
        # on_request emits the IPC event the frontend renders as a PermissionCard
        # and blocks until the user answers. In CLI/test mode a stub can auto-answer.
        self._on_request = on_request
        self._grants: dict[str, dict] = {}   # tool_id -> {granted_at, scope_details}
        self._denied: set[str] = set()

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
