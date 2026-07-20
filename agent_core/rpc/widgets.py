"""widget.* + stats.get handlers — the declarative widget rail (routine / stat /
command specs) and the core-computed stat sources it renders (engineering-spec §7,
§4.8; widgets.py invariants)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from agent_core.permissions.gate import PermissionStatus
from agent_core.policy import PolicyMode
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _SERVER_ERROR
from agent_core.tools.base import (
    ExecutionContext,
    call_is_destructive,
    call_permission_detail,
)
from agent_core.widgets import MAX_PINNED, validate_widget_spec, widget_summary


def _month_start_epoch() -> int:
    """Unix-epoch seconds for 00:00 on the first of the current month (UTC).

    'This month's tokens' is 'usage since this epoch' — the token meter sums
    ``usage_log`` rows at or after it. UTC matches how usage rows are stamped
    (``int(time.time())``)."""
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return int(start.timestamp())


class WidgetsMixin(ServerContext):
    # --- widgets + stats (declarative specs; core-computed sources) -------
    def _stats_get(self) -> dict:
        """stats.get -> the three core-computed sources. Carries NO key material:
        token totals, per-provider latency, and connection status only (§8.3)."""
        self._ensure_built()
        totals = self.store.usage_totals_since(_month_start_epoch())
        latency = self.store.latest_latency_per_provider()
        return {
            # No invented limit — v1 has no per-account cap to show (null).
            "tokensMonth": {"total": totals["total"], "limit": None},
            "providerLatency": latency,
            "connections": self._connections(latency),
        }

    def _widget_list(self) -> dict:
        """widget.list -> stored widgets, INVALID specs hidden at render (safety:
        a spec that fails validate_widget_spec is never surfaced or run)."""
        self._ensure_built()
        mode = self._mode()
        safe_mode = mode is PolicyMode.SAFE
        widgets: list[dict] = []
        for row in self.store.list_widgets():
            # Dev-created widgets are hidden while the Simple profile is active
            # (policy.py) — and command widgets also fail SAFE-mode validation below.
            if safe_mode and row.get("created_in_mode") == PolicyMode.OPEN.value:
                continue
            try:
                spec = json.loads(row["spec_json"])
            except ValueError:
                continue
            if validate_widget_spec(spec, mode) is not None:
                continue
            widgets.append(
                {
                    "id": row["id"],
                    "spec": spec,
                    "pinned": row["pinned"],
                    "position": row["position"],
                    # Display-only mode provenance for the frontend's "DEV" tag —
                    # never consulted for permissions (the gate re-checks at run).
                    "createdInMode": row.get("created_in_mode"),
                }
            )
        return {"widgets": widgets}

    def _widget_set_pinned(self, params: dict) -> dict:
        self._ensure_built()
        widget_id = params.get("id")
        if not widget_id or self.store.get_widget(widget_id) is None:
            return {"ok": False, "error": "That widget isn't here any more."}
        pinned = bool(params.get("pinned"))
        if pinned and self.store.count_pinned_widgets(exclude_id=widget_id) >= MAX_PINNED:
            return {"ok": False, "error": "You can pin up to six widgets. Unpin one first."}
        self.store.set_widget_pinned(widget_id, pinned)
        return {"ok": True}

    def _widget_delete(self, params: dict) -> dict:
        """widget.delete {id} -> {ok}. Idempotent — deleting an absent widget is fine.

        Hook H5 (G3): the snapshot comes FIRST, and a failed snapshot REFUSES the
        delete. The spec text exists nowhere else once the row is gone, so
        proceeding without a restore point is the one outcome the floor must not
        allow; refusing is recoverable, an unbackable delete is not. The existence
        check is what keeps a blind delete of an absent id from minting a row."""
        self._ensure_built()
        widget_id = params.get("id")
        if widget_id and self.store.get_widget(widget_id) is not None:
            if not self._snapshot_auto("widget_delete"):
                return {
                    "ok": False,
                    "error": "Addison couldn't save a restore point just now, so it "
                    "didn't delete anything. Try again in a moment.",
                }
            self.store.delete_widget(widget_id)
        return {"ok": True}

    def _handle_widget_run(self, params: dict, request_id) -> None:
        """widget.run — the rail's Run pill for a COMMAND widget (OPEN mode only).

        Routine and stat widgets refuse here: their actions already have homes
        (routine.run / stats.get). The command runs through the SAME registry +
        gate path as a routine command step, so the per-invocation destructive
        prompt holds — clicking a widget can never skip a card the chat would
        have shown. SAFE mode refuses before touching the registry (dev-created
        widgets are already hidden from SAFE lists; this is the belt for a stale
        frontend or a raced mode switch)."""
        self._ensure_built()
        widget_id = params.get("id")
        row = self.store.get_widget(widget_id) if widget_id else None
        if row is None:
            self._respond(request_id, {"ok": False, "error": "That widget isn't here any more."})
            return
        try:
            spec = json.loads(row["spec_json"])
        except ValueError:
            self._respond(request_id, {"ok": False, "error": "That widget can't run."})
            return
        if spec.get("kind") != "command":
            self._respond(
                request_id,
                {"ok": False, "error": "That widget doesn't run commands."},
            )
            return
        mode = self._mode()
        if mode is PolicyMode.SAFE:
            self._respond(
                request_id,
                {
                    "ok": False,
                    "error": "That widget uses developer abilities, so it's waiting in "
                    "Developer profile.",
                },
            )
            return
        tool = self.tool_registry.get("run_command")
        args = {"command": spec.get("command", "")}
        status = self.permission_gate.authorize(
            "run_command",
            mode=mode,
            destructive=call_is_destructive(tool, args),
            detail=call_permission_detail(tool, args),
        )
        if status != PermissionStatus.GRANTED:
            self._respond(
                request_id,
                {"ok": False, "error": "You declined a permission it needs."},
            )
            return
        context = ExecutionContext(
            conversation_id=f"widget:{widget_id}",
            shell_bridge=self._shell_bridge,
            policy_mode=mode,
        )
        try:
            result = tool.execute(args, context)
        except RuntimeError as exc:
            self._respond(request_id, {"ok": False, "error": str(exc)})
            return
        except Exception:
            self._respond(request_id, {"ok": False, "error": "That widget's command didn't work."})
            return
        # run_command truncates its own transcript output, so content passes through.
        self._respond(
            request_id,
            {"ok": result.success, "output": result.content}
            if result.success
            else {"ok": False, "error": result.content},
        )

    def _handle_widget_propose(self, request_id) -> None:
        """Draft a widget spec from the recent conversation (mirrors routine.propose:
        draft held in memory, nothing saved yet). v1 only proposes a routine widget
        (a routine just run or named) or a matching stat widget; otherwise refuses."""
        draft = self._draft_widget_from_conversation(self._mode())
        if draft is None:
            self._respond_error(request_id, _SERVER_ERROR, "I can't make a widget from this yet.")
            return
        self._draft_widget = draft
        self._respond(
            request_id,
            {
                "title": draft["title"],
                "kind": draft["kind"],
                "summary": widget_summary(draft),
                "spec": draft,
            },
        )

    def _handle_widget_confirm(self, params: dict, request_id) -> None:
        """widget.confirmSave {accept}: save the held draft ONLY on explicit accept.
        Saving a widget is LOW-risk (display-only), so no permission card — but the
        spec is re-validated here (never trust the held draft blindly)."""
        draft = self._draft_widget
        if draft is None:
            self._respond_error(
                request_id, _SERVER_ERROR, "There's no widget waiting to be added."
            )
            return
        if not params.get("accept"):
            self._draft_widget = None
            self._respond(request_id, {"ok": False, "declined": True})
            return
        mode = self._mode()
        error = validate_widget_spec(draft, mode)
        if error is not None:
            self._draft_widget = None
            self._respond_error(request_id, _SERVER_ERROR, error)
            return
        widget_id = str(uuid4())
        pinned = self.store.count_pinned_widgets() < MAX_PINNED
        self.store.insert_widget(
            id=widget_id,
            spec_json=json.dumps(draft),
            pinned=pinned,
            position=self.store.next_widget_position(),
            created_at=int(time.time()),
            created_in_mode=mode.value,
        )
        self._draft_widget = None
        self._respond(request_id, {"ok": True, "widgetId": widget_id, "pinned": pinned})

    def _draft_widget_from_conversation(self, mode: PolicyMode) -> dict | None:
        """The widget heuristic. Returns a valid spec dict or None (a refusal).

        Priority: an explicit ask for token/latency/connection info -> that stat
        widget; else (OPEN mode only) the last run_command in the recent chat -> a
        command widget; else the routine just run, or a routine named in the recent
        chat -> that routine widget; else None."""
        recent = self.conversation.messages[-10:]
        if mode is PolicyMode.OPEN:
            command = self._recent_command(recent)
            if command is not None:
                return {"kind": "command", "command": command, "title": command[:60]}
        joined = " ".join(
            m.content.lower()
            for m in recent
            if m.role == "user" and isinstance(m.content, str)
        )
        if any(k in joined for k in ("token", "usage", "how much have i used", "cost")):
            return {"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}
        if any(k in joined for k in ("latency", "how fast", "response time", "how quick")):
            return {"kind": "stat", "source": "provider_latency", "title": "Model latency"}
        if any(k in joined for k in ("connection", "connected", "online", "reachable")):
            return {"kind": "stat", "source": "connections", "title": "Connections"}
        if self._last_run_routine_id is not None:
            try:
                routine = self.routine_library.get(self._last_run_routine_id)
                return {"kind": "routine", "routineId": routine.id, "title": routine.name[:60]}
            except KeyError:
                pass
        for entry in self.routine_library.list():
            routine = entry["routine"]
            if routine.name and routine.name.lower() in joined:
                return {"kind": "routine", "routineId": routine.id, "title": routine.name[:60]}
        return None

    @staticmethod
    def _recent_command(messages: list) -> str | None:
        """The most recent run_command invocation in ``messages`` (OPEN mode only),
        so a command widget can be proposed from it. None if there is no such call."""
        command: str | None = None
        for message in messages:
            for call in getattr(message, "tool_calls", None) or []:
                if getattr(call, "tool_id", None) == "run_command":
                    value = (call.args or {}).get("command")
                    if isinstance(value, str) and value.strip():
                        command = value
        return command
