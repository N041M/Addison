"""widget.* + stats.get handlers — the declarative widget rail (routine / stat /
checklist / note / timer / command specs), the per-widget state the three
interactive kinds keep, and the core-computed stat sources it renders
(engineering-spec §7, §4.8; widgets.py invariants)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from agent_core.permissions.gate import PermissionStatus
from agent_core.policy import PolicyMode
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import (
    _SERVER_ERROR,
    _WIDGET_DEV_ABILITIES_MESSAGE,
    _unavailable_marker,
)
from agent_core.tools.base import (
    ExecutionContext,
    call_is_destructive,
    call_permission_detail,
)
from agent_core.widgets import (
    MAX_CHECKLIST_ITEMS,
    MAX_ITEM_LEN,
    MAX_NOTE_LEN,
    MAX_PINNED,
    MAX_TIMER_SECONDS,
    MAX_TITLE_LEN,
    initial_widget_state,
    plain_duration,
    validate_widget_spec,
    validate_widget_state,
    widget_summary,
    widget_uses_dev_abilities,
)

# What the person has to have typed for the core to draft one of the three
# interactive kinds. Deliberately small and literal: these fire on the USER's own
# words (never assistant content), and a loose pattern here means proposing the
# wrong widget, which the person then has to read and decline.
_CHECKLIST_WORDS = (
    "checklist", "check list", "to-do", "todo", "to do list",
    "shopping list", "packing list", "grocery list", "tick off",
)
# Word-boundaried so "footnote"/"noted" don't count as asking for a note widget.
_NOTE_RE = re.compile(r"\bnotes?\b|\bjot\b|\bscratchpad\b")
_TIMER_RE = re.compile(r"\btimer\b|\bcountdown\b|\bcount down\b")
# "- milk", "* milk", "1. milk", "2) milk" — one thing to do per line.
_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+(.*\S)")
# "25 minutes", "2 hr", "90 sec" — one plainly-written unit at a time.
_DURATION_RE = re.compile(r"(\d+)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b")


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
        # One read for the whole rail; a widget without a row simply has no state.
        states = self.store.widget_states()
        widgets: list[dict] = []
        for row in self.store.list_widgets():
            try:
                spec = json.loads(row["spec_json"])
            except ValueError:
                continue
            # Widgets that NEED developer abilities are LISTED while the Simple
            # profile is active, visibly disabled, instead of vanishing (owner
            # decision 2026-08-06; docs/SAFETY.md owns the rule). They return
            # untouched in Developer.
            #
            # Decided from the SPEC, never from ``created_in_mode``. The stamp
            # answers "where was this born", and a checklist born in Developer
            # needs nothing developer about it — under the stamp it arrived here
            # disabled, claiming abilities it does not use, with its boxes frozen.
            #
            # DISPLAY ONLY — this marker is not what stops the widget running.
            # _handle_widget_run refuses in SAFE before it touches the registry,
            # with this very sentence, and the gate re-checks underneath that. If
            # the flag and dispatch ever disagree, DISPATCH WINS.
            needs_dev = self._widget_needs_dev(spec)
            unavailable = _unavailable_marker(
                mode, needs_dev, _WIDGET_DEV_ABILITIES_MESSAGE
            )
            # Render-time validation, against the mode this widget would actually
            # run in: a row that needs developer abilities is judged by OPEN's
            # vocabulary, because that is the profile it is waiting for. A spec
            # that is broken THERE too is still dropped — a disabled card is for
            # work that is merely waiting, never for a row nothing can read.
            #
            # ``needs_dev`` is what a row IS, so a command spec behind a 'safe'
            # stamp (a restored config, an older build, a hand-edited database)
            # is caught here exactly like an honest one, and is listed-disabled
            # rather than hidden — the stamp cannot buy a row anything.
            spec_mode = PolicyMode.OPEN if needs_dev else mode
            if validate_widget_spec(spec, spec_mode) is not None:
                continue
            widget = {
                "id": row["id"],
                "spec": spec,
                "pinned": row["pinned"],
                "position": row["position"],
                # Display-only mode provenance for the frontend's "DEV" tag —
                # never consulted for permissions (the gate re-checks at run).
                "createdInMode": row.get("created_in_mode"),
            }
            # Absent entirely when the widget is usable — an available row keeps
            # exactly the shape older frontends already parse.
            if unavailable is not None:
                widget["unavailable"] = unavailable
            # Per-widget state (checklist / note / timer), RE-VALIDATED here on
            # the way out. A stored state that no longer fits its spec — a
            # checklist whose length disagrees, a hand-edited row — is DROPPED,
            # so the widget renders from its declaration rather than from
            # something nobody can vouch for. Absent for every other kind.
            state = self._valid_widget_state(spec, states.get(row["id"]))
            if state is not None:
                widget["state"] = state
            widgets.append(widget)
        return {"widgets": widgets}

    def _widget_needs_dev(self, spec) -> bool:
        """Does this widget need the Developer profile? Asked of the SPEC, and of
        whatever the spec POINTS AT — never of the row's ``created_in_mode``.

        Two ways to need it, and the second is why this lives here rather than in
        ``widgets.py``:

          * the spec itself is OPEN-only (``widget_uses_dev_abilities`` — today
            that is a ``command`` widget, tomorrow whatever else validates in OPEN
            alone);
          * the spec is a launcher for a routine that needs it. A
            ``{"kind": "routine"}`` spec is SAFE-legal by SHAPE whatever it points
            at, so without this look-through a dev routine's Run pill would sit
            live in the Simple rail and fail on every press — ``routine.run``
            refuses it, which is enforcement doing its job and a rail promising
            something it cannot deliver. The frontend already draws the pair this
            way (``WidgetRail``'s dev flag reads the routine's own provenance);
            this is the core agreeing rather than the two disagreeing quietly.

        The routine half still reads the routine's STAMP, because routines have not
        been converted yet — their availability is provenance-based end to end
        (``rpc/routines.py``), and answering this question a second, better way on
        the widget side would put the rail and the library in disagreement about
        the same routine. When routines convert, this line follows them, and it is
        the only line that has to."""
        if widget_uses_dev_abilities(spec):
            return True
        if not isinstance(spec, dict) or spec.get("kind") != "routine":
            return False
        routine_id = spec.get("routineId")
        if not isinstance(routine_id, str):
            return False
        return self.routine_library.created_in_mode(routine_id) == PolicyMode.OPEN.value

    @staticmethod
    def _valid_widget_state(spec: dict, state_json: str | None) -> dict | None:
        """Decode + validate one stored state against its spec, or None.

        None covers all three ways a widget has no state to show: no row, a row
        that will not decode, and a row that no longer matches the spec. The
        caller cannot tell them apart on purpose — every one of them means "draw
        the declaration"."""
        if state_json is None:
            return None
        try:
            state = json.loads(state_json)
        except ValueError:
            return None
        if validate_widget_state(spec, state) is not None:
            return None
        return state

    def _widget_set_state(self, params: dict) -> dict:
        """widget.setState {id, state} -> {ok, state?} | {ok:false, error}.

        The mutable half of the three interactive SAFE kinds. NO PERMISSION CARD,
        and that is not an omission: a checklist, a note and a timer invoke no
        tool, reach no file, and have no execution surface whatsoever — they are
        non-destructive BY CONSTRUCTION, which is exactly what SAFE invariant 4
        asks a SAFE widget to be. Adding a gate here would be asking the person
        for permission to tick their own box.

        What IS checked, in this order:
          * the widget exists;
          * the ACTIVE profile can use it — a widget that NEEDS developer
            abilities is listed in Simple but disabled, and dispatch is the
            enforcement, never the display marker (the widget.run rule, owner
            decision 2026-08-06). Asked of the spec, so the three stateful kinds
            are never caught by it: a checklist cannot be a command widget, and
            ticking a box you made on Tuesday must not depend on which profile
            was active when you made it;
          * its spec is still valid in this mode (the render-time check, so a
            spec the rail would refuse to draw cannot be written to either);
          * the state fits the SPEC — per kind, server-side, against the same
            closed vocabulary. The frontend's shape is never trusted.
        """
        self._ensure_built()
        widget_id = params.get("id")
        # Narrowed to `str` here rather than at the write: a non-string id can
        # never name a row, so it takes the same "isn't here any more" exit as an
        # id that simply doesn't exist.
        if not isinstance(widget_id, str) or not widget_id:
            return {"ok": False, "error": "That widget isn't here any more."}
        row = self.store.get_widget(widget_id)
        if row is None:
            return {"ok": False, "error": "That widget isn't here any more."}
        mode = self._mode()
        try:
            spec = json.loads(row["spec_json"])
        except ValueError:
            return {"ok": False, "error": "That widget can't be changed."}
        unavailable = _unavailable_marker(
            mode, self._widget_needs_dev(spec), _WIDGET_DEV_ABILITIES_MESSAGE
        )
        if unavailable is not None:
            return {"ok": False, "error": unavailable["message"]}
        if validate_widget_spec(spec, mode) is not None:
            return {"ok": False, "error": "That widget can't be changed."}
        state = params.get("state")
        error = validate_widget_state(spec, state)
        if error is not None:
            return {"ok": False, "error": error}
        self.store.set_widget_state(widget_id, json.dumps(state), int(time.time()))
        # Echo the STORED state back, so an optimistic frontend reconciles against
        # what the core actually holds rather than against what it hoped it sent.
        return {"ok": True, "state": state}

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
        have shown. SAFE mode refuses before touching the registry: dev-created
        widgets are LISTED in SAFE now (disabled, owner decision 2026-08-06), so
        this refusal is the enforcement, not a belt — the list marker is display
        only and a stale frontend, or a raced mode switch, lands here."""
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
            # The rail's SAFE refusal used to emit nothing at all, which made the
            # plan's "written on every branch including refusals" false at the one
            # site where a click, not a model, starts a command. Same outcome the
            # live loop and the engine record for a dev tool named outside OPEN.
            #
            # Audited by ID, and the lookup happens INSIDE the audit helper on
            # purpose: resolving the tool out here to build the row is what turned
            # this clean refusal into an error frame the first time it was written.
            # A refusal must not depend on what the registry happens to contain.
            self._audit_tool_by_id("run_command", spec, mode, "dev_only")
            self._respond(
                request_id,
                {"ok": False, "error": _WIDGET_DEV_ABILITIES_MESSAGE},
            )
            return
        tool = self.tool_registry.get("run_command")
        args = {"command": spec.get("command", "")}
        # THE HARDLINE DENYLIST (step 5.5, item 3), above the gate — the same check
        # the live loop and the routine engine run, at the third and last site a
        # command reaches a shell. A widget spec is persisted and model-authorable,
        # so a Run pill must never be the one door where a forbidden command is
        # merely a card away.
        forbidden = self._is_forbidden_call(tool, args)
        if forbidden is not None:
            self._audit_tool(tool, args, mode, "forbidden")
            self._respond(request_id, {"ok": False, "error": forbidden})
            return
        status = self.permission_gate.authorize(
            "run_command",
            mode=mode,
            destructive=call_is_destructive(tool, args),
            detail=call_permission_detail(tool, args),
            # Same resolution function the live loop + routine engine read (D3):
            # a Run pill can never skip a card (or add one) the chat would not.
            guards=self._effective_guards(),
            # trusted=False UNCONDITIONALLY (step 5, D5): a command widget is a
            # persisted, one-click, model-authorable spec, so it must ALWAYS card —
            # workspace trust never suppresses it. (run_command has no affected_path
            # anyway, so it is never confined or trust-suppressed regardless.)
            trusted=False,
        )
        if status != PermissionStatus.GRANTED:
            self._audit_tool(tool, args, mode, "denied")
            self._respond(
                request_id,
                {"ok": False, "error": "You declined a permission it needs."},
            )
            return
        self._audit_tool(tool, args, mode, "granted")
        context = ExecutionContext(
            conversation_id=f"widget:{widget_id}",
            shell_bridge=self._shell_bridge,
            policy_mode=mode,
            # Same allowlist the chat and a routine get (step 5.5, item 2) — a Run
            # pill must never be sandboxed more loosely than the conversation.
            trusted_roots=self._trusted_roots,
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

    def _audit_tool_by_id(self, tool_id, spec, mode, outcome) -> None:
        """Audit a refusal that happens BEFORE the tool is resolved.

        The registry lookup lives in here, under the same blanket ``except`` the
        row write does, because the callers are refusal paths: a Run pill that is
        being turned away must be turned away with a plain sentence whatever the
        registry holds. Resolving the tool in the handler to build the audit row
        is exactly how a clean SAFE refusal first became an error frame — every
        test server registers its own tools, and nothing promises ``run_command``
        is among them.

        A missing registration still leaves a row: the id is what the record is
        for, and ``detail`` is allowed to be empty."""
        try:
            tool = self.tool_registry.get(tool_id)
        except Exception:
            tool = None
        if tool is None:
            try:
                self._record_tool_audit(
                    {
                        "id": str(uuid4()),
                        "conversation_id": None,
                        "tool_id": tool_id,
                        "detail": None,
                        "mode": mode.value if hasattr(mode, "value") else str(mode),
                        "destructive": False,
                        "outcome": outcome,
                        "redacted": None,
                        "created_at": int(time.time()),
                    }
                )
            except Exception:
                pass
            return
        self._audit_tool(tool, {"command": spec.get("command", "")}, mode, outcome)

    def _audit_tool(self, tool, args, mode, outcome) -> None:
        """One tool-decision row for a Run pill (step 5.5, item 4). The rail is the
        third site a command can start, so it is the third site that must leave a
        record — a widget is persisted and model-authorable, and a Run pill that
        left no trace would be the quietest way to run something."""
        try:
            self._record_tool_audit(
                {
                    "id": str(uuid4()),
                    "conversation_id": None,
                    "tool_id": tool.definition.id,
                    "detail": call_permission_detail(tool, args),
                    "mode": mode.value if hasattr(mode, "value") else str(mode),
                    "destructive": bool(call_is_destructive(tool, args)),
                    "outcome": outcome,
                    "redacted": None,
                    "created_at": int(time.time()),
                }
            )
        except Exception:
            pass

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
        # A stateful kind starts in the state the CORE derives from its spec (an
        # un-ticked list, the note's declared text, a paused full-length timer) —
        # never in one the model or the frontend supplied. Written after the
        # widget row so the foreign key holds.
        initial = initial_widget_state(draft)
        if initial is not None:
            self.store.set_widget_state(widget_id, json.dumps(initial), int(time.time()))
        self._draft_widget = None
        self._respond(request_id, {"ok": True, "widgetId": widget_id, "pinned": pinned})

    def _draft_widget_from_conversation(self, mode: PolicyMode) -> dict | None:
        """The widget heuristic. Returns a valid spec dict or None (a refusal).

        Priority, and why this order (step 6 half A kept the existing one and slotted
        the three interactive kinds into the middle of it):

          1. (OPEN only) the last run_command in the recent chat -> a command widget;
          2. an explicit ask for token/latency/connection info -> that stat widget.
             These keywords stay ahead of the new kinds because they were already
             ahead of everything, and moving them would silently change what an
             existing phrase produces;
          3. an explicit ask for a checklist / note / timer -> that widget. Above
             the routine fallback DELIBERATELY: that fallback fires on the mere
             existence of a routine run this session, whatever the person just
             said, so a checklist asked for after any routine run would never be
             reached below it;
          4. the routine just run, or a routine named in the recent chat;
          5. otherwise None (a refusal — the card is simply not shown).

        Everything is read from the USER's messages only, never from assistant
        content: the model does not get to author a spec, it only gets asked."""
        recent = self.conversation.messages[-10:]
        if mode is PolicyMode.OPEN:
            command = self._recent_command(recent)
            if command is not None:
                return {"kind": "command", "command": command, "title": command[:60]}
        user_texts = [
            m.content for m in recent if m.role == "user" and isinstance(m.content, str)
        ]
        joined = " ".join(text.lower() for text in user_texts)
        if any(k in joined for k in ("token", "usage", "how much have i used", "cost")):
            return {"kind": "stat", "source": "tokens_month", "title": "Tokens this month"}
        if any(k in joined for k in ("latency", "how fast", "response time", "how quick")):
            return {"kind": "stat", "source": "provider_latency", "title": "Model latency"}
        if any(k in joined for k in ("connection", "connected", "online", "reachable")):
            return {"kind": "stat", "source": "connections", "title": "Connections"}
        interactive = self._draft_interactive_widget(user_texts, joined)
        if interactive is not None:
            return interactive
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

    @classmethod
    def _draft_interactive_widget(cls, user_texts: list[str], joined: str) -> dict | None:
        """A checklist / note / timer drafted from what the PERSON typed, or None.

        Titles are FIXED plain phrases rather than a slice of the utterance: a
        title cut out of "can you make me a widget with a shopping list on it"
        reads like a transcription error, and the person is looking straight at
        the proposal card either way.

        A checklist with no items is not proposed at all — items are fixed at
        creation (see widgets.py), so an empty one could never become useful, and
        falling through to the next rule is better than saving a dead widget."""
        if any(word in joined for word in _CHECKLIST_WORDS):
            items = cls._checklist_items(user_texts)
            if items:
                return {"kind": "checklist", "items": items, "title": "Checklist"}
        if _TIMER_RE.search(joined):
            seconds = cls._timer_seconds(joined)
            return {
                "kind": "timer",
                "seconds": seconds,
                "title": f"{plain_duration(seconds)} timer"[:MAX_TITLE_LEN],
            }
        if _NOTE_RE.search(joined):
            return {"kind": "note", "text": cls._note_text(user_texts), "title": "Note"}
        return None

    @staticmethod
    def _checklist_items(user_texts: list[str]) -> list[str]:
        """The things to tick, out of the most recent user message that names a
        list. Two shapes only, both of them ways people actually write one: a
        bulleted/numbered line each, or a comma-separated run after a colon.
        Capped at MAX_CHECKLIST_ITEMS and MAX_ITEM_LEN — a spec that would fail
        validation must never be proposed."""
        for text in reversed(user_texts):
            if not any(word in text.lower() for word in _CHECKLIST_WORDS):
                continue
            candidates: list[str] = []
            for line in text.splitlines():
                bullet = _BULLET_RE.match(line.strip())
                if bullet:
                    candidates.append(bullet.group(1))
            if not candidates and ":" in text:
                tail = text.split(":", 1)[1]
                candidates = re.split(r",|\band\b|;", tail)
            items = [
                item.strip()[:MAX_ITEM_LEN]
                for item in candidates
                if item.strip()
            ]
            if items:
                return items[:MAX_CHECKLIST_ITEMS]
        return []

    @staticmethod
    def _timer_seconds(joined: str) -> int:
        """The length asked for ("25 minute timer", "an hour and a half" is NOT
        parsed — one unit, plainly written), else five minutes. Clamped to the
        validator's ceiling so a "1000 hour timer" becomes a saveable one."""
        total = 0
        for value, unit in _DURATION_RE.findall(joined):
            multiplier = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
            total += int(value) * multiplier
        if total <= 0:
            return 300
        return min(total, MAX_TIMER_SECONDS)

    @staticmethod
    def _note_text(user_texts: list[str]) -> str:
        """The note's INITIAL text: whatever followed the colon in the message
        that asked for it, else blank. The person edits it in the rail from
        there — the state holds the current text, the spec holds this one."""
        for text in reversed(user_texts):
            if _NOTE_RE.search(text.lower()) and ":" in text:
                return text.split(":", 1)[1].strip()[:MAX_NOTE_LEN]
        return ""

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
