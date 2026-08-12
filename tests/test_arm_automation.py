"""arm_automation / disarm_automation and the keyword gate (step 8, phase 3).

This is the commit where "Addison authors; the OS runs" stops being half a
sentence, so most of what is under test is the machinery that stands between a
model asking and a job existing on somebody's computer:

  * **The door**, every refusal, all of them ABOVE the gate. A call that cannot
    succeed must never be shown to a person as a ceremony they might perform —
    and a row whose command the fence has learned to refuse since it was written
    must not be armable now, which is what ``command_text`` buys.
  * **The code.** Minted per request, shown once, retyped; three wrong answers deny;
    a wrong answer re-shows the card with one fewer attempt rather than failing
    silently. And the property the whole thing rests on: **it exists nowhere the
    model can read it** — not in a tool_result, not in ``tool_audit``, not in the
    transcript, not in any table.
  * **The registrations, which are deliberately different.** ``arm_automation`` is
    undo-ENFORCED because its undo is real; ``disarm_automation`` takes the waiver
    because it genuinely has none, and registering it LOW to dodge the check would
    be a false statement about a tool that changes what the OS is running.
  * **Where it may be asked from.** Live conversation only: a routine step and a
    widget's Run pill are refused with a plain sentence, because a stored one-click
    spec that can raise a code card is the autopilot the code exists to break.

Every guard below names its mutation. The pairs were run: mutate, watch the named
test die, restore.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from agent_core import automation_nonce
from agent_core import main as main_module
from agent_core.automations import LABEL_PREFIX
from agent_core.main import build_registry
from agent_core.memory.store import Store
from agent_core.orchestrator import Conversation, Message, Orchestrator
from agent_core.permissions.gate import (
    tool_requires_arming,
    PermissionGate,
    PermissionStatus,
    call_arming_card,
    call_arming_refusal,
)
from agent_core.policy import OS_AUTOMATION_DIRS, GuardConfig, PolicyMode
from agent_core.providers.base import (
    ModelResponse,
    ModelRole,
    ProviderCapabilities,
    ToolCallRequest,
)
from agent_core.providers.router import ModelRouter
from agent_core.routines.engine import RoutineEngine
from agent_core.routines.model import Routine, RoutineStep
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.arm_automation import (
    LAUNCH_AGENTS_DIR,
    WARNINGS,
    ArmAutomationTool,
    arming_is_supported,
    install_path,
)
from agent_core.tools.arm_automation import (
    _NO_SUCH_AUTOMATION as _ARM_NO_SUCH,
)
from agent_core.tools.arm_automation import (
    _AMBIGUOUS_NAME as _ARM_AMBIGUOUS,
)
from agent_core.tools.arm_automation import (
    _NO_USABLE_SCHEDULE,
    _NOT_READY,
    _ONLY_IN_DEVELOPER,
    _ONLY_ON_A_MAC,
    _UNDO_NOT_READY,
)
from agent_core.tools.arm_automation import (
    _NO_SHELL as _ARM_NO_SHELL,
)
from agent_core.tools.base import (
    FORBIDDEN_CALL_ARMING,
    ActionSnapshot,
    ExecutionContext,
    RiskTier,
)
from agent_core.tools.disarm_automation import DisarmAutomationTool
from agent_core.tools.disarm_automation import (
    _NO_SUCH_AUTOMATION as _DISARM_NO_SUCH,
)
from agent_core.tools.disarm_automation import (
    _AMBIGUOUS_NAME as _DISARM_AMBIGUOUS,
)
from agent_core.tools.registry import DEV_ONLY_REFUSAL, LIVE_ONLY_REFUSAL, ToolRegistry
from tests.conftest import IPC_DB_NAME, ShellBridgeStubs, _shutdown, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ARM_SRC = _REPO_ROOT / "agent_core" / "tools" / "arm_automation.py"
_DISARM_SRC = _REPO_ROOT / "agent_core" / "tools" / "disarm_automation.py"
_BRIDGE_SRC = _REPO_ROOT / "agent_core" / "shell_bridge.py"
_WIDGETS_RPC_SRC = _REPO_ROOT / "agent_core" / "rpc" / "widgets.py"

_ROW = {
    "id": "auto-1",
    "name": "Tidy up downloads",
    "label": f"{LABEL_PREFIX}tidy-downloads",
    "command": "/usr/bin/find ~/Downloads -mtime +30 -delete",
    "schedule_kind": "interval",
    "schedule_json": json.dumps({"minutes": 60}),
    "created_in_mode": "open",
    "created_at": 1_700_000_000,
}


def _row(**overrides: Any) -> dict:
    return {**_ROW, **overrides}


@pytest.fixture
def on_a_mac(monkeypatch) -> None:
    """Arming exists on macOS and nowhere else, so every test that wants the happy
    path has to SAY it is on a Mac — otherwise the suite would pass for the wrong
    reason on a Linux runner (every call refused) and for the right one on a
    laptop. The refusal itself is tested by forcing the other answer."""
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def store(tmp_path) -> Any:
    # The live store gets its OWN directory, deliberately not the one
    # ADDISON_DB_PATH points at (conftest points that at tmp_path itself), so the
    # denylist checks below are asked against the live store rather than a derived
    # one — test_create_automation's fixture, for the same reason.
    live = tmp_path / "live"
    live.mkdir()
    store = Store(live / "automations.sqlite3")
    store.insert_automation(**_ROW)
    yield store
    store.close()


class _FakeArmBridge(ShellBridgeStubs):
    """Records what the shell was asked to arm. Everything else raises."""

    def __init__(self, ok: bool = True, error: str | None = None) -> None:
        self.armed: list[tuple] = []
        self.disarmed: list[str] = []
        self.armed_labels: list[str] = []
        self._ok = ok
        self._error = error

    def arm_automation(
        self, label: str, command: str, schedule_kind: str, schedule: dict
    ) -> dict:
        self.armed.append((label, command, schedule_kind, schedule))
        if not self._ok:
            return {"ok": False, "error": self._error} if self._error else {"ok": False}
        self.armed_labels.append(label)
        return {"ok": True}

    def disarm_automation(self, label: str) -> dict:
        self.disarmed.append(label)
        return {"ok": True}

    def list_armed(self) -> dict:
        return {"armed": list(self.armed_labels), "supported": True}


def _tool(store: Store, bridge: Any = None) -> ArmAutomationTool:
    return ArmAutomationTool(store_ref=lambda: store, shell_bridge=bridge)


def _off(store: Store) -> DisarmAutomationTool:
    return DisarmAutomationTool(store_ref=lambda: store)


def _ctx(mode: PolicyMode = PolicyMode.OPEN, bridge: Any = None) -> ExecutionContext:
    return ExecutionContext(conversation_id="c1", policy_mode=mode, shell_bridge=bridge)


# ===========================================================================
# (1) The registrations — and why the two are deliberately not the same
# ===========================================================================


def test_neither_tool_exists_in_the_simple_view_and_both_do_in_developer():
    """SAFE invariant 1: an automation's payload is a shell command, so the whole
    subsystem is OPEN's (plan §5.3). Hiding is not enforcing, so the dispatch
    refusal is asserted beside the visibility.

    Mutation: drop ``open_only``/``dev_only`` from either registration in
    ``build_registry`` — the SAFE assertion for that tool fails."""
    registry = build_registry()
    safe = {d.id for d in registry.visible_tools(PolicyMode.SAFE)}
    open_view = {d.id for d in registry.visible_tools(PolicyMode.OPEN)}
    for tool_id in ("arm_automation", "disarm_automation"):
        assert tool_id not in safe, tool_id
        assert tool_id in open_view, tool_id
        assert registry.is_dev_only(tool_id), tool_id
        assert registry.refuse_if_dev_only_outside_open(tool_id, PolicyMode.SAFE), tool_id
        assert registry.refuse_if_dev_only_outside_open(tool_id, PolicyMode.OPEN) is None


def test_arm_is_registered_undo_enforced_because_its_undo_is_real(monkeypatch):
    """``dev_only=True`` would ALSO waive the undo-at-registration check — the single
    most important check in the codebase — for a tool that does not need the waiver:
    arming's undo is a genuine disarm. ``open_only=True`` gives the same visibility
    and keeps the check (registry R3, ``write_project_file``'s shape).

    Proved behaviourally: a version of the tool with no ``undo`` must fail to
    register through the very function main uses.

    Mutation: change ``open_only=True`` to ``dev_only=True`` for ArmAutomationTool in
    ``build_registry`` — the undo-less tool registers happily and this fails."""

    class _NoUndo(ArmAutomationTool):
        undo: Any = None

    monkeypatch.setattr(main_module, "ArmAutomationTool", _NoUndo)
    with pytest.raises(ValueError, match="no undo"):
        build_registry()


def test_disarm_has_no_undo_at_all_and_says_so_by_taking_the_waiver():
    """THE DECISION THIS FILE MOST NEEDS TO PIN. Undoing a DISARM would be an ARM
    performed by the UndoManager — no card, no preview, no typed code — which is the
    ceremony walked around from the inside. So there is no ``undo`` method, and the
    registration takes ``allow_missing_undo`` (via ``dev_only``) rather than
    pretending the tier is LOW.

    Registering it LOW would also have "worked", and that is the trap: LOW means
    read-only (``RiskTier``), and this changes what the operating system runs. SAFE
    invariant 2's own text is explicit — a tool that cannot be undone "stays LOW AND
    READ-ONLY". This one is not read-only.

    The waiver is proved to be what is doing the work: without it, registration
    RAISES.

    Mutation: add a no-op ``undo`` to DisarmAutomationTool (first assertion), or
    register it ``open_only=True`` instead of ``dev_only=True`` (last block)."""
    assert getattr(DisarmAutomationTool, "undo", None) is None
    assert DisarmAutomationTool.definition.risk_tier is RiskTier.HIGH

    registry = ToolRegistry()
    with pytest.raises(ValueError, match="no undo"):
        registry.register(_off(None), open_only=True)  # type: ignore[arg-type]
    # ...and with the waiver it registers, hidden from SAFE.
    registry.register(_off(None), dev_only=True, live_only=True)  # type: ignore[arg-type]
    assert registry.list_for_model() == []
    assert registry.is_dev_only("disarm_automation")


def test_both_tools_are_live_only_and_arm_is_high_and_destructive():
    """The three per-call answers dispatch asks, stated once so a later edit to any
    of them is visible: every call cards (nothing about an automation makes arming
    it safe enough to skip one), nothing is path-bounded (the file belongs to the
    shell — and a non-None ``affected_path`` could mark the call ``trusted``, which
    is the flag that SKIPS a card), and neither may be named by a saved spec.

    Mutation: drop ``live_only=True`` from either registration — the last block
    fails, and so do the two routine tests below."""
    registry = build_registry()
    assert registry.refuse_if_live_only("arm_automation") == LIVE_ONLY_REFUSAL
    assert registry.refuse_if_live_only("disarm_automation") == LIVE_ONLY_REFUSAL
    # Not vacuous — an ordinary tool is unaffected.
    assert registry.refuse_if_live_only("calculator") is None

    arm = _tool(None)  # type: ignore[arg-type]
    assert arm.definition.risk_tier is RiskTier.HIGH
    assert arm.is_destructive({}) is True
    assert arm.affected_path({}) is None
    off = _off(None)  # type: ignore[arg-type]
    assert off.is_destructive({}) is True
    assert off.affected_path({}) is None


class _Exploding:
    """``arm_automation``'s shape with a door and a preview that both fail — the
    transient-store-error case. It DECLARES ``arming_card``, which is what makes it
    a tool that requires the ceremony however badly its preview goes."""

    def arming_refusal(self, args: dict) -> str | None:
        raise RuntimeError("database is locked")

    def arming_card(self, args: dict) -> dict | None:
        raise RuntimeError("database is locked")


class _ExplodingGate(PermissionGate):
    """Any call to ``authorize`` is a failure — a refused call is not a card anybody
    may approve (test_step_5_5_containment's rule, applied to arming)."""

    def authorize(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a refused arming call reached the permission gate")


@pytest.mark.parametrize("tool_id", ["arm_automation", "disarm_automation"])
def test_a_simple_profile_turn_cannot_reach_either_tool(store: Store, on_a_mac, tool_id):
    """Hiding is not enforcing: a tool_use naming a hidden id still reaches dispatch,
    and the gate does not check dev-ness. The registry's dispatch refusal is what
    holds, above the gate and above ``execute``.

    Mutation: remove the ``refuse_if_dev_only_outside_open`` call from the
    orchestrator's dispatch — the gate is reached and explodes."""
    registry = build_registry(store_ref=lambda: store)

    class _Provider:
        def __init__(self) -> None:
            self._responses = [
                ModelResponse(
                    text=None,
                    tool_calls=[ToolCallRequest(id="c1", tool_id=tool_id, args={"id": "auto-1"})],
                ),
                ModelResponse(text="ok", tool_calls=[]),
            ]

        def capabilities(self) -> ProviderCapabilities:
            return ProviderCapabilities(
                native_tool_calling=True, max_context_tokens=100_000,
                supports_streaming=False, runs_off_device=False,
            )

        def send(self, messages, tools, effort=None, timeout=None, on_delta=None):
            return self._responses.pop(0)

    orchestrator = Orchestrator(
        model_router=ModelRouter(configured={ModelRole.PRIMARY: _Provider()}),
        tool_registry=registry,
        permission_gate=_ExplodingGate(),
        undo_manager=UndoManager(store=store, tool_registry=registry),
    )
    conversation = Conversation(id="c1")
    conversation.messages.append(Message(role="user", content="go"))
    orchestrator.run_turn(conversation, mode=PolicyMode.SAFE)
    answer = next(m for m in conversation.messages if m.role == "tool")
    assert str(answer.content) == DEV_ONLY_REFUSAL


def test_the_descriptions_tell_the_person_what_arming_really_means():
    """The card is the defence, but the DESCRIPTION is what the model repeats. A
    description that dropped "even when Addison is closed" would make this sound
    smaller than it is, in Addison's own voice.

    Mutation: delete either clause from ``ArmAutomationTool.definition.description``."""
    text = ArmAutomationTool.definition.description.lower()
    assert "even when addison is closed" in text
    assert "type a short code" in text
    assert "mac only" in text
    for jargon in ("launchd", "plist", "launchctl", "nonce", "registry", "dev_only"):
        assert jargon not in text, jargon
    # And the one thing switching OFF must say: it is not a delete.
    assert "stays saved" in DisarmAutomationTool.definition.description.lower()


# ===========================================================================
# (2) The door — every refusal, and none of them a card
# ===========================================================================


def test_off_a_mac_the_whole_capability_says_so_plainly(store: Store, monkeypatch):
    """Arming is launchd and nothing else (plan §5.4). Everywhere else one plain
    sentence that does not pretend a workaround exists — the automation stays
    written down, which is true on every platform.

    Mutation: make ``arming_is_supported`` return True unconditionally — both
    assertions fail."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert arming_is_supported() is False
    assert _tool(store).arming_refusal({"id": "auto-1"}) == _ONLY_ON_A_MAC
    # The two sentences differ because the two situations do — "nothing was switched
    # on" and "there was nothing to switch off" are different facts, and one message
    # for both would be wrong for one of them.
    off = str(_off(store).arming_refusal({"id": "auto-1"}))
    assert "on a Mac" in off and "switch off" in off


@pytest.mark.parametrize(
    "args",
    [{"id": "nope"}, {"id": ""}, {"id": 7}, {}, {"id": None}],
    ids=["unknown", "empty", "number", "missing", "none"],
)
def test_an_automation_nothing_matches_is_refused(store: Store, on_a_mac, args):
    """The id comes from a model reading a list that may have moved on — a restore,
    another window, a removal. A card for an automation nobody can name is a
    ceremony about nothing.

    Mutation: return the row unconditionally from ``_row``."""
    assert _tool(store).arming_refusal(args) == _ARM_NO_SUCH
    assert _off(store).arming_refusal(args) == _DISARM_NO_SUCH


def test_the_refusal_never_announces_a_deletion_it_cannot_know_about(store: Store, on_a_mac):
    """KNOWN-BUGS P1 #1, the half that made the bug a LIE rather than only a dead
    end. Both sentences used to read "that automation isn't saved any more" — a
    claim about a deletion, made in the one situation where nothing was deleted:
    somebody gave a name, the row sat in Settings, and Addison told them it was gone.

    Addison knows it found no match. That is all it may say, plus where the real
    name is written down.

    Mutation: put the old sentence back — the "any more" assertions fail."""
    for sentence in (_ARM_NO_SUCH, _DISARM_NO_SUCH):
        lowered = sentence.lower()
        assert "any more" not in lowered
        assert "couldn't find" in lowered
        # The next step, which is the whole reason a person can recover from this.
        assert "settings" in lowered
        # Still no machinery, in either direction.
        for jargon in ("uuid", "id from the row", "launchd", "plist", "database", "query"):
            assert jargon not in lowered, jargon
    # The two are not one message doing double duty: what did not happen differs.
    assert "switched on" in _ARM_NO_SUCH and "switched off" in _DISARM_NO_SUCH


def test_an_automation_can_be_armed_by_its_name_when_only_one_has_it(store: Store, on_a_mac):
    """THE FIX FOR KNOWN-BUGS P1 #1. Arming took an id and nothing else, while every
    route a person actually has carries a NAME — the Settings "Arm…" button seeds a
    sentence with the name in it. So the one path the feature exists for could never
    resolve anything, and every attempt was answered with the deletion sentence
    above.

    End-to-end at the tool layer: the door opens, the card previews the right row,
    and the shell is handed that row's label.

    Mutation: drop the name branch from ``resolve_automation`` — all four fail."""
    bridge = _FakeArmBridge()
    tool = _tool(store, bridge)
    by_name = {"id": "Tidy up downloads"}

    assert tool.arming_refusal(by_name) is None
    card = tool.arming_card(by_name)
    assert card is not None and card["automationName"] == "Tidy up downloads"
    result = tool.execute(by_name, _ctx(bridge=bridge))
    assert result.success is True
    assert bridge.armed[0][0] == _ROW["label"]
    # The denylist still reads the ROW's command, whichever way the row was named.
    assert tool.command_text(by_name) == _ROW["command"]

    # And back off again through the same resolution, so a person who switched
    # something on by name can switch that same thing off by name.
    off = _off(store).execute(by_name, _ctx(bridge=bridge))
    assert off.success is True
    assert bridge.disarmed == [_ROW["label"]]


def test_a_name_matches_exactly_and_an_id_still_wins(store: Store, on_a_mac):
    """Exact, after stripping — never a prefix, never case-folded. Arming runs a
    command unattended and unconfined, so "closest match" is not a standard this
    door may use: a near-miss that refuses costs one sentence, and a near-miss that
    resolves costs the wrong job on somebody's computer.

    Mutation: lower-case or ``startswith`` the comparison — the near-miss loop
    fails."""
    tool = _tool(store)
    for near in ("tidy up downloads", "Tidy up", "Tidy up downloads please", "Tidy  up downloads"):
        assert tool.arming_refusal({"id": near}) == _ARM_NO_SUCH, near
    # Surrounding whitespace is not a different name — it is what typing looks like.
    assert tool.arming_refusal({"id": "  Tidy up downloads  "}) is None
    # The id is asked FIRST and answers alone: a row whose NAME is another row's id
    # cannot divert a call that named that id.
    store.insert_automation(**_row(id="auto-2", name="auto-1", label=f"{LABEL_PREFIX}confusing"))
    card = tool.arming_card({"id": "auto-1"})
    assert card is not None and card["automationName"] == "Tidy up downloads"


def test_two_automations_with_one_name_refuse_rather_than_pick(store: Store, on_a_mac):
    """Names are NOT unique — ``derive_label`` gives the second "Tidy up downloads"
    a label of its own precisely so both rows can exist. Picking one would be arming
    a command the person did not choose, so the answer is a question instead.

    The sentence names nothing but the problem: no command, no label, no id — the
    list is where those live.

    Mutation: return ``matches[0]`` when several match — the refusals become None."""
    store.insert_automation(**_row(id="auto-2", label=f"{LABEL_PREFIX}tidy-downloads-2"))
    tool = _tool(store)
    by_name = {"id": "Tidy up downloads"}

    assert tool.arming_refusal(by_name) == _ARM_AMBIGUOUS
    assert _off(store).arming_refusal(by_name) == _DISARM_AMBIGUOUS
    for sentence in (_ARM_AMBIGUOUS, _DISARM_AMBIGUOUS):
        assert "more than one" in sentence.lower()
        assert "id" in sentence.lower()
        assert _ROW["command"] not in sentence and _ROW["label"] not in sentence
    # Nothing is armed by a refused call, and the card is never built for one.
    bridge = _FakeArmBridge()
    assert _tool(store, bridge).execute(by_name, _ctx(bridge=bridge)).success is False
    assert bridge.armed == []
    # ...and the id still works, which is what the sentence tells them to use.
    assert tool.arming_refusal({"id": "auto-2"}) is None


def test_an_id_that_is_not_a_string_never_becomes_a_database_query(on_a_mac):
    """The type check in ``_row`` earns its place ABOVE the query rather than behind
    the blanket ``except``, and the difference is only visible from the store's side
    — which is why this test watches the store rather than the answer.

    Both spellings refuse, so a test asserting only the sentence cannot tell them
    apart; what the guard buys is that a model's ``{"id": {...}}`` is answered as a
    wrong id instead of becoming an ``sqlite3.InterfaceError`` that the catch-all
    then has to make indistinguishable from a database that is genuinely broken.

    Mutation: drop the ``isinstance`` check from ``_row`` — ``asked`` fills up."""

    class _Spy:
        db_path = "/tmp/does-not-matter/x.sqlite3"

        def __init__(self) -> None:
            self.asked: list = []

        def get_automation(self, automation_id):
            self.asked.append(automation_id)
            return None

    spy = _Spy()
    tool = ArmAutomationTool(store_ref=lambda: spy)  # type: ignore[arg-type]
    for args in ({"id": 7}, {"id": {"a": 1}}, {}, {"id": None}, {"id": ""}):
        assert tool.arming_refusal(args) == _ARM_NO_SUCH
    assert spy.asked == []
    # ...and a real id does reach the store, so this is not passing by refusing
    # everything.
    assert tool.arming_refusal({"id": "auto-1"}) == _ARM_NO_SUCH
    assert spy.asked == ["auto-1"]


def test_a_schedule_nothing_could_run_is_refused_before_the_card(store: Store, on_a_mac):
    """A row the vocabulary cannot read installs a job with NO trigger: launchd
    loads it and never fires it. Arming that is a ceremony performed for nothing, so
    it is refused where the person can be told what to do instead.

    The row is written by hand because the authoring door refuses it — which is the
    point: rows outlive the door that wrote them (a hand edit, an older build, a
    payload restored from a sidecar).

    Mutation: drop the ``schedule_is_readable`` branch from ``arming_refusal``."""
    store.insert_automation(**_row(id="auto-broken", label=f"{LABEL_PREFIX}broken",
                                   schedule_json=json.dumps({"minutes": 0})))
    assert _tool(store).arming_refusal({"id": "auto-broken"}) == _NO_USABLE_SCHEDULE
    # DISARM does NOT ask this question, deliberately: a job whose schedule nobody
    # can read is exactly the job somebody most wants to be able to switch off.
    assert _off(store).arming_refusal({"id": "auto-broken"}) is None


def test_before_a_store_exists_it_says_so_rather_than_guessing(on_a_mac):
    """The pre-store window (``build_registry`` runs before the worker thread builds
    the Store). An honest sentence, and — crucially — it is checked BEFORE the
    platform, because "Addison isn't ready" is true and "there is no such
    automation" would be a claim about a table nobody has opened."""
    assert _tool(None).arming_refusal({"id": "auto-1"}) == _NOT_READY  # type: ignore[arg-type]
    assert _tool(None).arming_card({"id": "auto-1"}) is None  # type: ignore[arg-type]
    assert _tool(None).command_text({"id": "auto-1"}) is None  # type: ignore[arg-type]


def test_a_row_the_fence_has_since_learned_to_refuse_cannot_be_armed(store: Store, on_a_mac):
    """THE REASON ``command_text`` IS DECLARED. Phase 2's door refuses a command that
    hands work to the OS — but a denylist is a list, and lists grow. A row written
    before the fence learned a program must not be armable today, so the SAME check
    is asked again at arming, from the row, at every dispatch site above the gate.

    Mutation: delete ``command_text`` from ArmAutomationTool — the call reaches the
    gate instead of being refused (and the turn-level test below explodes)."""
    store.insert_automation(**_row(id="auto-bad", label=f"{LABEL_PREFIX}bad",
                                   command="crontab -e"))
    tool = _tool(store)
    assert tool.command_text({"id": "auto-bad"}) == "crontab -e"
    assert tool.execute({"id": "auto-bad"}, _ctx()).content == FORBIDDEN_CALL_ARMING
    # DISARM declares no ``command_text`` AT ALL, and that asymmetry is deliberate:
    # a refusal here would leave a person unable to switch off the very job they
    # most want switched off.
    assert getattr(_off(store), "command_text", None) is None


def test_the_command_text_comes_from_the_row_and_never_from_the_call(store: Store):
    """A caller that could pass its own command text would be a caller passing a
    command to arm. The only thing ``args`` decides is WHICH row.

    Mutation: read ``args.get("command")`` in ``command_text``."""
    tool = _tool(store)
    assert tool.command_text({"id": "auto-1", "command": "rm -rf /"}) == _ROW["command"]


def test_outside_the_developer_profile_execute_refuses_on_its_own(store: Store, on_a_mac):
    """Belt-and-suspenders (``run_command``'s): the SAFE view never surfaces this and
    dispatch refuses it outside OPEN, but a tool that hands a job to the operating
    system asks the question itself.

    Mutation: delete the ``policy_mode`` branch from either ``execute``."""
    bridge = _FakeArmBridge()
    assert _tool(store, bridge).execute(
        {"id": "auto-1"}, _ctx(PolicyMode.SAFE, bridge)
    ).content == _ONLY_IN_DEVELOPER
    assert _off(store).execute({"id": "auto-1"}, _ctx(PolicyMode.SAFE, bridge)).success is False
    assert bridge.armed == [] and bridge.disarmed == []


def test_with_no_desktop_shell_nothing_is_armed(store: Store, on_a_mac):
    """The core has no OS permissions of its own (spec §1.3). No bridge means no
    arming — never a fallback, because there is no second way to do this."""
    result = _tool(store).execute({"id": "auto-1"}, _ctx(bridge=None))
    assert result.success is False and result.content == _ARM_NO_SHELL
    assert result.snapshot is None


# ===========================================================================
# (3) What a granted arm actually sends, and what it can take back
# ===========================================================================


def test_arming_sends_four_typed_fields_and_never_a_document(store: Store, on_a_mac):
    """Plan §5.8. The shell BUILDS the plist from these fields, validates the label
    prefix, and writes only its own file. What crosses the bridge is a label, a
    command, a kind and that kind's numbers — the same projection the person read on
    the card, so the preview and the payload cannot describe different jobs.

    Mutation: pass ``json.loads(row.schedule_json)`` instead of ``schedule_fields``
    — a hand-edited row's stray keys then ride across the boundary."""
    store.insert_automation(**_row(id="auto-junk", label=f"{LABEL_PREFIX}junk",
                                   schedule_json=json.dumps({"minutes": 5, "sneaky": "x"})))
    bridge = _FakeArmBridge()
    result = _tool(store, bridge).execute({"id": "auto-junk"}, _ctx(bridge=bridge))
    assert result.success is True
    label, command, kind, schedule = bridge.armed[0]
    assert label == f"{LABEL_PREFIX}junk"
    assert command == _ROW["command"]
    assert kind == "interval"
    assert schedule == {"minutes": 5}


def test_the_answer_the_model_relays_repeats_both_warnings(store: Store, on_a_mac):
    """The model's summary is what the person re-reads afterwards, and a summary that
    quietly drops "even when Addison is closed" makes this sound smaller than it is.

    Mutation: drop the warnings from ``_armed_text``."""
    bridge = _FakeArmBridge()
    text = str(_tool(store, bridge).execute({"id": "auto-1"}, _ctx(bridge=bridge)).content)
    assert "Tidy up downloads" in text
    assert "every hour" in text
    for warning in WARNINGS:
        assert warning in text


def test_a_shell_that_refuses_leaves_nothing_behind(store: Store, on_a_mac):
    """The shell owns the last word — a bad label, an unwritable directory,
    ``launchctl`` saying no. Its sentence is relayed when it gave one, and there is
    NO snapshot, because there is nothing to undo.

    Mutation: return a snapshot regardless of ``ok`` — undo would then try to
    disarm a job that was never installed."""
    refusing = _FakeArmBridge(ok=False, error="That job name is already in use.")
    result = _tool(store, refusing).execute({"id": "auto-1"}, _ctx(bridge=refusing))
    assert result.success is False
    assert result.content == "That job name is already in use."
    assert result.snapshot is None
    # ...and a refusal with no sentence still becomes one plain sentence.
    silent = _FakeArmBridge(ok=False)
    quiet = _tool(store, silent).execute({"id": "auto-1"}, _ctx(bridge=silent))
    assert quiet.success is False and "wouldn't take that automation" in str(quiet.content)


def test_undo_switches_the_job_back_off(store: Store, on_a_mac):
    """The rare non-LOW tool whose undo is honest: one file was installed under one
    label, and removing it puts the computer back where it was. This is what lets
    the registration stay undo-ENFORCED.

    The payload is the LABEL and nothing else, so undo still works if the row is
    removed in between — which is exactly when undo matters.

    Mutation: make ``undo`` a no-op — the disarm assertion fails."""
    bridge = _FakeArmBridge()
    tool = _tool(store, bridge)
    result = tool.execute({"id": "auto-1"}, _ctx(bridge=bridge))
    assert result.snapshot is not None
    assert result.snapshot.tool_id == "arm_automation"
    assert result.snapshot.undo_payload == {"label": f"{LABEL_PREFIX}tidy-downloads"}

    store.delete_automation("auto-1")
    tool.undo(result.snapshot)
    assert bridge.disarmed == [f"{LABEL_PREFIX}tidy-downloads"]


def test_undo_refuses_a_payload_that_does_not_name_one_of_addisons_own_labels(store: Store):
    """A snapshot is replayed with no gate and no person, so its payload is the one
    thing standing between the UndoManager and an arbitrary ``disarmAutomation``.
    The shell validates the prefix too; refusing here means a malformed payload
    never becomes a request at all.

    Mutation: drop the ``startswith(LABEL_PREFIX)`` check from ``undo``."""
    bridge = _FakeArmBridge()
    tool = _tool(store, bridge)
    for payload in ({"label": "com.someone.else"}, {"label": 7}, {}):
        with pytest.raises(RuntimeError, match=_UNDO_NOT_READY[:20]):
            tool.undo(ActionSnapshot(
                id="s", tool_call_id="", tool_id="arm_automation",
                undo_payload=payload, created_at=1,
            ))
    assert bridge.disarmed == []
    # ...and with no bridge injected at construction it refuses rather than
    # silently reporting success (undo gets no ExecutionContext to fall back on).
    with pytest.raises(RuntimeError, match=_UNDO_NOT_READY[:20]):
        _tool(store).undo(ActionSnapshot(
            id="s", tool_call_id="", tool_id="arm_automation",
            undo_payload={"label": f"{LABEL_PREFIX}tidy-downloads"}, created_at=1,
        ))


def test_disarming_leaves_the_automation_saved_and_records_no_snapshot(store: Store, on_a_mac):
    """A tightening: the job stops, the row stays. "Switched off" and "deleted" are
    different things, and somebody who thinks they deleted it will be surprised to
    find it on the list.

    Mutation: return a snapshot from ``DisarmAutomationTool.execute`` — the
    UndoManager would then be able to re-arm with no card and no code."""
    bridge = _FakeArmBridge()
    result = _off(store).execute({"id": "auto-1"}, _ctx(bridge=bridge))
    assert result.success is True
    assert result.snapshot is None
    assert bridge.disarmed == [f"{LABEL_PREFIX}tidy-downloads"]
    assert "still saved" in str(result.content)
    assert [row.id for row in store.list_automations()] == ["auto-1"]


# ===========================================================================
# (4) The card's preview — the whole truth, not a summary
# ===========================================================================


def test_the_card_carries_everything_a_person_needs_to_decide(store: Store, on_a_mac):
    """Plan §3: the name, when it runs in plain words, the EXACT command, where the
    file goes, and the two frozen sentences. The code's whole job is to make
    somebody read this, so a preview that summarised would defeat the ceremony at
    its one moment.

    Mutation: drop any key from ``arming_card`` — the matching assertion fails."""
    card = _tool(store).arming_card({"id": "auto-1"})
    # Exactly these five keys and no sixth — and the command rides WHOLE,
    # unshortened and unredacted. `automation.list` made the same call for the same
    # reason, and this is the surface where it matters.
    assert card == {
        "automationName": "Tidy up downloads",
        "scheduleSentence": "Every hour",
        "command": _ROW["command"],
        "installPath": f"{LAUNCH_AGENTS_DIR}/{LABEL_PREFIX}tidy-downloads.plist",
        "warnings": list(WARNINGS),
    }


def test_the_two_warnings_are_frozen_word_for_word():
    """These are the only two sentences in the app that must survive every redesign
    (plan §3, §5.7). Both are true: launchd owns the job the moment it is installed,
    and the seatbelt confines ADDISON's commands, never somebody's own automation.

    A warning a surface can reword is a warning a surface can soften, which is why
    they are core-owned and pinned byte-for-byte on both sides.

    Mutation: reword either string."""
    assert WARNINGS == (
        "This will run on its own schedule even when Addison is closed.",
        "It runs outside Addison's sandbox.",
    )


def test_the_install_path_is_shown_with_a_tilde_and_sits_inside_the_fence():
    """Two properties in one line of code.

    The tilde is not cosmetic: this string lands on a card, in a screenshot of that
    card, and in whatever somebody pastes when they ask for help — the expanded form
    embeds their account name and answers no question.

    And the directory must be a member of ``policy.OS_AUTOMATION_DIRS``, the phase-1
    fence: the folder Addison writes an armed job into is exactly the folder no
    workspace may trust, no command may name and no seatbelt profile may allow. One
    owner for that list; this module names one member of it.

    Mutation: point ``LAUNCH_AGENTS_DIR`` anywhere else."""
    assert LAUNCH_AGENTS_DIR.startswith("~/")
    assert LAUNCH_AGENTS_DIR in OS_AUTOMATION_DIRS
    assert install_path("com.addison.auto.x") == f"{LAUNCH_AGENTS_DIR}/com.addison.auto.x.plist"


def test_disarming_raises_an_ordinary_card_with_no_code_on_it(store: Store, on_a_mac):
    """§5.10's other half: never trap somebody out of turning something off. A person
    who mistypes a code three times must not be left with a job still running.

    ``call_arming_card`` answering None is what makes the gate take the ordinary
    per-invocation path.

    Mutation: give DisarmAutomationTool an ``arming_card`` — this fails, and
    switching an automation off starts asking for a code."""
    assert call_arming_card(_off(store), {"id": "auto-1"}) is None
    assert call_arming_card(_tool(store), {"id": "auto-1"}) is not None


def test_a_tool_whose_door_or_preview_explodes_is_refused_rather_than_escalated():
    """A preview that fails must not become a card with nothing above it to read —
    and must not become an ORDINARY card either, which is the failure this test was
    renamed and rewritten for (adversarial review, 2026-08-07).

    THE BUG THE OLD VERSION MISSED. It asserted only that the two helpers return
    ``None`` without raising — which is the MECHANISM by which the call was not
    refused. ``authorize`` took the arming path iff a preview ARRIVED, so a tool
    whose ``arming_card`` raised (``_row`` swallows every store error, so a
    transient SQLite failure is enough) fell through to the ordinary destructive
    branch: one plain Allow button and no code. Under Custom's
    ``auto_grant_scope='everything'`` it fell through to NO CARD AT ALL — verbatim
    the failure ``authorize``'s docstring says the arming ordering exists to
    prevent. A test named "is refused" asserted the opposite of its own name.

    The requirement is now a property of the TOOL (``tool_requires_arming``), so a
    missing preview denies.

    Mutation: revert ``authorize``'s ``requires_arming and arming is None`` guard,
    or make ``tool_requires_arming`` return False."""
    exploding = _Exploding()
    # The helpers still fail soft — that part was right, and a raising gate would
    # be its own defect.
    assert call_arming_refusal(exploding, {}) is None
    assert call_arming_card(exploding, {}) is None
    # ...but the tool still REQUIRES the ceremony, because that is about the tool.
    assert tool_requires_arming(exploding) is True

    cards: list[str] = []

    def _handler(tool_id, detail=None):
        cards.append(tool_id)
        return PermissionStatus.GRANTED

    # Every guard configuration, including the two that skip cards entirely: a lost
    # preview must deny under all of them, not merely under the defaults.
    for guards in (
        None,
        GuardConfig(auto_grant_scope="everything"),
        GuardConfig(destructive_card="session"),
    ):
        gate = PermissionGate(on_request=_handler)
        status = gate.authorize(
            "arm_automation",
            mode=PolicyMode.OPEN,
            destructive=True,
            arming=call_arming_card(exploding, {}),
            requires_arming=tool_requires_arming(exploding),
            guards=guards,
        )
        assert status == PermissionStatus.DENIED, guards
    # Not one card was raised, so nobody was asked to approve a thing Addison could
    # not describe — and nothing was armed.
    assert cards == []


def test_a_tool_that_needs_no_ceremony_is_completely_unaffected():
    """The freeze, in the same breath: every tool but ``arm_automation`` declares no
    ``arming_card``, so it requires nothing and its path through the gate is what it
    always was. Without this the fix above could be satisfied by denying everything.

    Mutation: make ``tool_requires_arming`` return True unconditionally."""

    class _Ordinary:
        pass

    assert tool_requires_arming(_Ordinary()) is False
    gate = PermissionGate(on_request=lambda tool_id, detail=None: PermissionStatus.GRANTED)
    assert (
        gate.authorize(
            "save_file",
            mode=PolicyMode.OPEN,
            destructive=False,
            requires_arming=tool_requires_arming(_Ordinary()),
        )
        == PermissionStatus.GRANTED
    )


# ===========================================================================
# (5) The gate — the one card no setting may soften
# ===========================================================================


class _RecordingGateHandler:
    """An ``on_request`` that records how it was asked and answers as told."""

    def __init__(self, answer: PermissionStatus = PermissionStatus.GRANTED) -> None:
        self.calls: list[tuple] = []
        self._answer = answer

    def __call__(self, tool_id, detail=None, arming=None) -> PermissionStatus:
        self.calls.append((tool_id, detail, arming))
        return self._answer


@pytest.mark.parametrize(
    "guards",
    [
        GuardConfig(),
        GuardConfig(auto_grant_scope="everything"),
        GuardConfig(destructive_card="session"),
        GuardConfig(auto_grant_scope="everything", destructive_card="session"),
    ],
    ids=["defaults", "never-ask", "ask-once", "both"],
)
def test_no_custom_guard_can_turn_the_keyword_card_off(guards):
    """THE NONCE SHIPS NON-TUNABLE (plan §5.9), and the Custom panel's two dials are
    the ways it would otherwise have been lost:

      * ``auto_grant_scope='everything'`` auto-grants DESTRUCTIVE calls too, so
        arming would have happened with no card at all;
      * ``destructive_card='session'`` remembers an approval, so the SECOND
        automation of a session would arm on the strength of a code typed for the
        FIRST.

    Both are prompting guards; this is a floor. The arming branch is answered before
    either is read.

    Mutation: move the ``if arming is not None`` branch in ``authorize`` below the
    ``PolicyMode.OPEN`` block — the first two cases stop carding."""
    handler = _RecordingGateHandler()
    gate = PermissionGate(on_request=handler)
    preview = {"automationName": "x", "warnings": list(WARNINGS)}
    for _ in range(2):
        assert gate.authorize(
            "arm_automation", mode=PolicyMode.OPEN, destructive=True,
            guards=guards, arming=preview,
        ) is PermissionStatus.GRANTED
    # TWO cards for two calls — never remembered, in any configuration.
    assert len(handler.calls) == 2
    assert all(call[2] == preview for call in handler.calls)
    assert gate.auto_grants == []


def test_the_arming_card_is_never_remembered_as_a_grant():
    """Arming twice means typing twice, forever. A grant recorded in ``_grants``
    would also leak into the SAFE ``check()`` path, which is the shape [R2] exists
    to prevent.

    Mutation: call ``self.grant(tool_id)`` on the granted branch of
    ``_request_arming``."""
    gate = PermissionGate(on_request=_RecordingGateHandler())
    gate.authorize("arm_automation", mode=PolicyMode.OPEN, destructive=True,
                   arming={"automationName": "x"})
    assert gate.check("arm_automation") is PermissionStatus.NOT_YET_ASKED


def test_a_declined_arm_is_not_re_asked_for_the_rest_of_the_turn():
    """The don't-nag rule, unchanged: "not now" means not now. It must not become a
    way to ask somebody to type a code again and again in one turn.

    Mutation: drop the ``_denied`` check from ``_request_arming``."""
    handler = _RecordingGateHandler(PermissionStatus.DENIED)
    gate = PermissionGate(on_request=handler)
    for _ in range(3):
        assert gate.authorize("arm_automation", mode=PolicyMode.OPEN, destructive=True,
                              arming={"automationName": "x"}) is PermissionStatus.DENIED
    assert len(handler.calls) == 1
    gate.clear_denials()
    gate.authorize("arm_automation", mode=PolicyMode.OPEN, destructive=True,
                   arming={"automationName": "x"})
    assert len(handler.calls) == 2


def test_an_ordinary_call_is_byte_for_byte_what_it_was():
    """The freeze: ``arming=None`` is every previous call unchanged, and a handler
    that never opted into arming is still called with TWO arguments."""
    seen: list[tuple] = []

    def two_arg_handler(tool_id, detail=None) -> PermissionStatus:
        seen.append((tool_id, detail))
        return PermissionStatus.GRANTED

    gate = PermissionGate(on_request=two_arg_handler)
    assert gate.authorize("run_command", mode=PolicyMode.OPEN, destructive=True,
                          detail="ls") is PermissionStatus.GRANTED
    assert seen == [("run_command", "ls")]


# ===========================================================================
# (6) THE CODE — minted, shown, retyped, and nowhere a model can read it
# ===========================================================================
#
# These drive the REAL round-trip: a running server, real `permission.requestGrant`
# frames out, real `permission.respond` frames in. The nonce lives in `main`, so a
# unit test of the gate could not see it at all.


def _cards(harness) -> list[dict]:
    return [f for f in list(harness.writer.frames) if f.get("method") == "permission.requestGrant"]


def _wait_for_card(harness, n: int) -> dict:
    harness.writer.wait_for(lambda _frame: len(_cards(harness)) >= n)
    return _cards(harness)[n - 1]


def _answer(harness, request_id: int, allow: bool = True, typed: object = None) -> None:
    params: dict = {"toolId": "spy_tool", "allow": allow}
    if typed is not None:
        params["typed"] = typed
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": "permission.respond", "params": params}
    )


def _ask(harness, arming: dict | None = None) -> list:
    """Run one ``_on_permission_request`` on its own thread and collect the answer."""
    out: list = []

    def run() -> None:
        out.append(harness.server._on_permission_request("spy_tool", None, arming))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return [thread, out]


_PREVIEW = {
    "automationName": "Tidy up downloads",
    "scheduleSentence": "Every hour",
    "command": "/usr/bin/find ~/Downloads -mtime +30 -delete",
    "installPath": f"{LAUNCH_AGENTS_DIR}/x.plist",
    "warnings": list(WARNINGS),
}


def test_the_right_code_grants_and_the_card_carried_the_whole_preview(tmp_path):
    """The happy path, end to end on real frames: the card carries the preview plus
    the two fields the caller owns, and retyping the code grants.

    Mutation: compare with ``==`` on the raw typed value instead of
    ``automation_nonce.matches`` — the lower-case answer below stops working."""
    h = build_server(tmp_path)
    try:
        thread, out = _ask(h, _PREVIEW)
        card = _wait_for_card(h, 1)["params"]
        arming = card["arming"]
        for key, value in _PREVIEW.items():
            assert arming[key] == value, key
        assert arming["attemptsLeft"] == automation_nonce.MAX_ATTEMPTS
        code = arming["nonce"]
        assert len(code) == automation_nonce.LENGTH + 1 and code[3] == "-"

        # Typed the way a person types it: lower case, no separator.
        _answer(h, 1, allow=True, typed=code.replace("-", "").lower())
        thread.join(timeout=5)
        assert out == [PermissionStatus.GRANTED]
    finally:
        _shutdown(h.reader, h.thread)


def test_a_wrong_code_re_shows_the_card_with_one_fewer_attempt(tmp_path):
    """Somebody who mistyped needs to be TOLD they mistyped — a ceremony that fails
    silently is one people stop trusting. The card comes back with the SAME code
    (they are copying it from the screen) and one fewer attempt.

    Mutation: return DENIED on the first mismatch instead of looping — only one card
    is ever emitted and this fails."""
    h = build_server(tmp_path)
    try:
        thread, out = _ask(h, _PREVIEW)
        first = _wait_for_card(h, 1)["params"]["arming"]
        _answer(h, 1, allow=True, typed="AAA-AAA")

        second = _wait_for_card(h, 2)["params"]["arming"]
        assert second["nonce"] == first["nonce"]
        assert second["attemptsLeft"] == automation_nonce.MAX_ATTEMPTS - 1
        assert second["command"] == _PREVIEW["command"]

        _answer(h, 2, allow=True, typed=first["nonce"])
        thread.join(timeout=5)
        assert out == [PermissionStatus.GRANTED]
    finally:
        _shutdown(h.reader, h.thread)


def test_three_wrong_codes_deny_the_request_and_stop_asking(tmp_path):
    """The budget is the thing that makes starting over — with a NEW code — the only
    strategy, rather than guessing being merely slow. Three cards, then denial, and
    no fourth card.

    IT TERMINATES, which is the property worth stating: ``attempts_left`` starts at
    MAX_ATTEMPTS, strictly decreases on the only looping branch, and the loop exits
    at zero.

    Mutation: drop the ``attempts_left <= 0`` return — the loop never ends and this
    test hangs to its timeout."""
    h = build_server(tmp_path)
    try:
        thread, out = _ask(h, _PREVIEW)
        for attempt in range(automation_nonce.MAX_ATTEMPTS):
            card = _wait_for_card(h, attempt + 1)["params"]["arming"]
            assert card["attemptsLeft"] == automation_nonce.MAX_ATTEMPTS - attempt
            _answer(h, attempt + 1, allow=True, typed="AAA-AAA")
        thread.join(timeout=5)
        assert out == [PermissionStatus.DENIED]
        assert len(_cards(h)) == automation_nonce.MAX_ATTEMPTS
    finally:
        _shutdown(h.reader, h.thread)


def test_saying_no_ends_it_at_once_whatever_is_in_the_code_field(tmp_path):
    """"Not now" is an answer, not a wrong code: the remaining attempts belong to
    somebody trying to say yes.

    Mutation: check the code before the ``allow`` flag — declining then costs two
    more cards."""
    h = build_server(tmp_path)
    try:
        thread, out = _ask(h, _PREVIEW)
        _wait_for_card(h, 1)
        _answer(h, 1, allow=False, typed="AAA-AAA")
        thread.join(timeout=5)
        assert out == [PermissionStatus.DENIED]
        assert len(_cards(h)) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_every_request_mints_a_code_of_its_own(tmp_path):
    """Single-use, per request (plan §3). Starting over is what mints a fresh code,
    and it is why a pre-written guess is worthless rather than merely unlikely.

    Mutation: hoist ``mint()`` to a module constant or an instance field — the two
    codes become equal."""
    h = build_server(tmp_path)
    try:
        codes = []
        for index in range(2):
            thread, out = _ask(h, _PREVIEW)
            card = _wait_for_card(h, index + 1)["params"]["arming"]
            codes.append(card["nonce"])
            _answer(h, index + 1, allow=True, typed=card["nonce"])
            thread.join(timeout=5)
            assert out == [PermissionStatus.GRANTED]
        assert codes[0] != codes[1]
    finally:
        _shutdown(h.reader, h.thread)


@pytest.mark.parametrize(
    "typed", [None, 42, {"code": "x"}, "", "   "], ids=["missing", "number", "object", "", "spaces"]
)
def test_an_answer_that_is_not_a_code_is_a_wrong_answer_not_a_crash(tmp_path, typed):
    """The webview sends this field; a frame that arrives malformed must cost an
    attempt rather than an exception on the worker thread — a crash there leaves the
    turn with no tool_result at all.

    Mutation: drop the ``isinstance`` guard in ``automation_nonce.normalise`` (the
    ``42`` and ``{}`` cases raise), or compare an empty expected as equal (the empty
    cases grant)."""
    h = build_server(tmp_path)
    try:
        thread, out = _ask(h, _PREVIEW)
        for attempt in range(automation_nonce.MAX_ATTEMPTS):
            _wait_for_card(h, attempt + 1)
            _answer(h, attempt + 1, allow=True, typed=typed)
        thread.join(timeout=5)
        assert out == [PermissionStatus.DENIED]
    finally:
        _shutdown(h.reader, h.thread)


# --- the whole turn, and the property everything rests on --------------------


class _ArmingProvider:
    """Asks for ``arm_automation`` once, then finishes."""

    def __init__(self, automation_id: str = "auto-1") -> None:
        self._responses = [
            ModelResponse(
                text=None,
                tool_calls=[ToolCallRequest(
                    id="c1", tool_id="arm_automation", args={"id": automation_id}
                )],
            ),
            ModelResponse(text="Done.", tool_calls=[]),
        ]
        self.histories: list[list] = []

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        self.histories.append(list(messages))
        return self._responses.pop(0)


def _arming_server(tmp_path, bridge, *, row: dict | None = None):
    """A live server whose registry carries the real ``arm_automation`` and whose
    database already holds one automation. Seeded through ``store_factory`` — on the
    worker thread, which is the thread that owns the SQLite connection."""
    made: dict[str, Store] = {}

    def factory() -> Store:
        store = Store(tmp_path / IPC_DB_NAME)
        store.set_setting("widgets_seeded", "1")
        store.insert_automation(**(row or _ROW))
        made["store"] = store
        return store

    tool = ArmAutomationTool(store_ref=lambda: made.get("store"), shell_bridge=bridge)
    return build_server(
        tmp_path,
        tool=tool,
        bridge=bridge,  # type: ignore[arg-type]  # a full-Protocol fake IS a bridge
        provider=_ArmingProvider(),
        store_factory=factory,
    )


def _rpc(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def test_the_code_reaches_the_person_and_nothing_else_ever_sees_it(tmp_path, monkeypatch):
    """**THE TEST THIS WHOLE FILE EXISTS FOR.** A real turn arms a real automation
    through the real gate, and afterwards the code appears in exactly one place: the
    card frame that carried it to the webview.

    Not in the tool_result the model is handed. Not in ``tool_audit``. Not in the
    transcript. Not in any table. A nonce a model can read is a nonce a model can
    type, which is the entire thing the ceremony prevents — and the property is
    structural rather than careful, because nothing outside ``_ask_with_keyword``
    holds a reference to the value.

    Mutation: put ``nonce`` into the ``detail`` passed to ``authorize`` (it lands in
    the audit row), or append it to the tool's result text — the sweep below names
    the table it found it in."""
    monkeypatch.setattr(sys, "platform", "darwin")
    bridge = _FakeArmBridge()
    h = _arming_server(tmp_path, bridge)
    try:
        assert _rpc(h, "profile.set", {"profileId": "developer"}, 900)["mode"] == "open"
        h.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "conversation.sendMessage",
                       "params": {"text": "switch on my tidy-up"}})
        card = _wait_for_card(h, 1)["params"]
        code = card["arming"]["nonce"]
        assert card["toolId"] == "arm_automation"
        assert card["arming"]["command"] == _ROW["command"]
        _answer_arm(h, 2, code)
        reply = h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
    finally:
        _shutdown(h.reader, h.thread)

    assert bridge.armed_labels == [f"{LABEL_PREFIX}tidy-downloads"]
    assert code not in json.dumps(reply)

    # The sweep: every value in every row of every table in the live database.
    connection = sqlite3.connect(tmp_path / IPC_DB_NAME)
    connection.row_factory = sqlite3.Row
    tables = [
        r[0] for r in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "tool_audit" in tables and "messages" in tables, tables
    for table in tables:
        for row in connection.execute(f"SELECT * FROM {table}").fetchall():  # noqa: S608
            for value in tuple(row):
                assert code not in str(value), f"the arming code was stored in {table}"
    audit = connection.execute("SELECT tool_id, outcome FROM tool_audit").fetchall()
    connection.close()
    assert ("arm_automation", "granted") in [tuple(r) for r in audit]


def _answer_arm(harness, request_id: int, code: str) -> None:
    harness.reader.feed({
        "jsonrpc": "2.0", "id": request_id, "method": "permission.respond",
        "params": {"toolId": "arm_automation", "allow": True, "typed": code},
    })


def test_declining_the_card_arms_nothing_and_is_audited_as_denied(tmp_path, monkeypatch):
    """The other outcome the log has to be able to say. "Not now" is a decision
    somebody made about the strongest action in the app, and it belongs in the same
    record as the yeses — the audit table is what can answer "was this ever armed?"
    afterwards, and an absent row reads as "nothing happened".

    Mutation: drop the ``denied`` audit call from the orchestrator's DENIED branch."""
    monkeypatch.setattr(sys, "platform", "darwin")
    bridge = _FakeArmBridge()
    h = _arming_server(tmp_path, bridge)
    try:
        _rpc(h, "profile.set", {"profileId": "developer"}, 900)
        h.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "conversation.sendMessage",
                       "params": {"text": "switch it on"}})
        _wait_for_card(h, 1)
        h.reader.feed({
            "jsonrpc": "2.0", "id": 2, "method": "permission.respond",
            "params": {"toolId": "arm_automation", "allow": False},
        })
        h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
    finally:
        _shutdown(h.reader, h.thread)
    assert bridge.armed == []
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        rows = [r for r in store.list_tool_audit() if r["tool_id"] == "arm_automation"]
        assert [r["outcome"] for r in rows] == ["denied"]
        # The row names the automation, never the command and never the code.
        assert rows[0]["detail"] == "Tidy up downloads"
    finally:
        store.close()


def test_a_refused_arm_never_becomes_a_card_and_is_audited_as_forbidden(tmp_path, monkeypatch):
    """The door, at the live dispatch site: a row whose command the fence refuses is
    turned away ABOVE the gate, so no card is ever raised — and the audit row is the
    only place that refusal is recorded at all.

    Mutation: delete ``command_text`` from ArmAutomationTool, or move the arming-door
    branch below ``authorize`` in the orchestrator — a card appears and this fails."""
    monkeypatch.setattr(sys, "platform", "darwin")
    bridge = _FakeArmBridge()
    h = _arming_server(tmp_path, bridge, row=_row(command="crontab -e"))
    try:
        _rpc(h, "profile.set", {"profileId": "developer"}, 900)
        h.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "conversation.sendMessage",
                       "params": {"text": "switch it on"}})
        h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert _cards(h) == []
    finally:
        _shutdown(h.reader, h.thread)
    assert bridge.armed == []
    store = Store(tmp_path / IPC_DB_NAME)
    try:
        rows = [r for r in store.list_tool_audit() if r["tool_id"] == "arm_automation"]
        assert [r["outcome"] for r in rows] == ["forbidden"]
    finally:
        store.close()


def test_an_automation_that_is_gone_never_becomes_a_card_either(tmp_path, monkeypatch):
    """The other half of the door at the live site: the row is not there, so there is
    nothing to preview and nobody should be asked to type anything.

    Mutation: drop the ``arming_refusal`` branch from the orchestrator — a card is
    raised with an empty preview."""
    monkeypatch.setattr(sys, "platform", "darwin")
    bridge = _FakeArmBridge()
    made: dict[str, Store] = {}

    def factory() -> Store:
        store = Store(tmp_path / IPC_DB_NAME)
        store.set_setting("widgets_seeded", "1")
        made["store"] = store
        return store

    tool = ArmAutomationTool(store_ref=lambda: made.get("store"), shell_bridge=bridge)
    h = build_server(
        tmp_path,
        tool=tool,
        bridge=bridge,  # type: ignore[arg-type]  # a full-Protocol fake IS a bridge
        provider=_ArmingProvider(),
        store_factory=factory,
    )
    try:
        _rpc(h, "profile.set", {"profileId": "developer"}, 900)
        h.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "conversation.sendMessage",
                       "params": {"text": "switch it on"}})
        h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert _cards(h) == []
    finally:
        _shutdown(h.reader, h.thread)
    assert bridge.armed == []


# ===========================================================================
# (7) Not from a saved spec — routines and widgets
# ===========================================================================


def _run_step(tool_id: str, store: Store, rows: list | None = None):
    if store.get_routine("r-1") is None:
        # routine_runs has a foreign key onto routines, so the run has to belong to
        # a saved routine — which is the honest shape anyway: only a SAVED routine
        # can be replayed, and that is precisely what this refusal is about.
        store.insert_routine(
            id="r-1", name="T", description="", plan_json={},
            created_from_conversation_id=None, created_at=1, created_in_mode="open",
        )
    registry = build_registry(store_ref=lambda: store)
    engine = RoutineEngine(
        registry,
        PermissionGate(on_request=_never_asked),
        UndoManager(store=store, tool_registry=registry),
        store=store,
        on_tool_audit=None if rows is None else rows.append,
    )
    return engine.run(
        Routine(
            id="r-1", name="T", description="", variables=[],
            steps=[RoutineStep("s1", tool_id, {"id": "auto-1"})],
        ),
        {},
        mode=PolicyMode.OPEN,
    )


def _never_asked(*args, **kwargs):  # pragma: no cover - must never run
    raise AssertionError("a routine step raised a permission card for an arming tool")


@pytest.mark.parametrize("tool_id", ["arm_automation", "disarm_automation"])
def test_a_saved_routine_step_can_neither_arm_nor_disarm(store: Store, on_a_mac, tool_id):
    """Plan §5.10. The ceremony belongs where a person is present and reading; a
    stored, one-click, model-authorable spec that could raise a code card mid-run
    invites answering it on autopilot, which is the reflex the code exists to break.

    Refused ABOVE the gate — the gate here explodes if it is ever consulted — and
    shaped as a failed STEP so ``on_failure`` still decides what happens next.

    SAFE-3 note: this NARROWS what a routine may do relative to live chat, which is
    the permitted direction.

    Mutation: delete the ``refuse_if_live_only`` branch from the routine engine, or
    drop ``live_only=True`` from either registration in ``build_registry``."""
    rows: list = []
    result = _run_step(tool_id, store, rows)
    assert result.status == "failed"
    assert result.detail == LIVE_ONLY_REFUSAL
    assert [(r["tool_id"], r["outcome"]) for r in rows] == [(tool_id, "not_callable")]
    # ...and the harness is not vacuous: an ordinary dev tool still runs its step.
    assert _run_step("create_automation", store).status in ("completed", "failed")


def test_a_widget_has_no_way_to_name_an_arming_tool_at_all(store: Store, on_a_mac):
    """The widget half of §5.10, and it is answered STRUCTURALLY rather than by a
    branch that could never fire: ``widget.run`` dispatches ONE hard-coded tool id.
    A widget is a command pill, a routine pill or a stat readout — no spec anywhere
    in the vocabulary carries a tool id, so a rail can reach an arming tool only by
    running a ROUTINE, and that route lands on the refusal above.

    Writing a ``refuse_if_live_only`` call into ``rpc/widgets.py`` beside a literal
    ``"run_command"`` would be a branch no test could ever execute — an unverifiable
    guard, which this repo treats as worse than none. The pin is the literal itself.

    Mutation: make ``_handle_widget_run`` resolve a tool id out of the spec — the
    source assertion fails and asks for the runtime check to come with it."""
    source = _WIDGETS_RPC_SRC.read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_handle_widget_run"
    )
    dispatched = {
        node.args[0].value if isinstance(node.args[0], ast.Constant) else "<computed>"
        for node in ast.walk(handler)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"get", "find"}
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "tool_registry"
        and node.args
    }
    assert dispatched == {"run_command"}, (
        "widget.run now resolves a tool id other than the hard-coded run_command. "
        "The rail is a dispatch site: give it registry.refuse_if_live_only(tool_id) "
        "above the gate, and a test that exercises it."
    )
    for tool_id in ("arm_automation", "disarm_automation"):
        assert tool_id not in source


# ===========================================================================
# (8) The core cannot send a document, and cannot build one to send
# ===========================================================================


def _string_literals(tree: ast.AST) -> list[str]:
    """Every string literal that is NOT a docstring. Prose explaining why arming
    lives in the shell is exactly what should be written in these files; a gate that
    refused the explanation would be a gate that punished it."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize("path", [_ARM_SRC, _DISARM_SRC, _BRIDGE_SRC], ids=lambda p: p.name)
def test_no_module_on_the_arming_path_can_assemble_a_plist(path: Path):
    """Plan §5.8 as a source-level pin. The shell BUILDS the XML from typed fields;
    the core sends numbers and strings. A document assembled here — or the preview
    builder imported here — would be the first sign somebody was assembling the
    escape rather than asking the shell for it, and the boundary would be one commit
    from becoming ``run_command`` with extra steps.

    ``.plist`` as a filename suffix is fine and expected (the card says where the
    file goes); what is refused is document STRUCTURE and the machinery for making
    it.

    Mutation: import ``plist_text`` into either tool, or add ``plistlib`` — this
    fails naming the file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] != "plistlib", path.name
        elif isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            assert "plist_text" not in imported, (
                f"{path.name} imports the preview builder: the shell builds its own "
                "document from typed fields and never accepts one (plan §5.8)"
            )
    for literal in _string_literals(tree):
        for marker in ("<?xml", "<plist", "</plist", "<key>", "DOCTYPE", "launchctl"):
            assert marker not in literal, f"{path.name} carries {marker!r} in a string"


def test_the_arm_bridge_call_sends_exactly_four_typed_fields():
    """The wire shape, read out of the source rather than out of a fake, because the
    fake is written by the same hand as the caller. Four keys, no fifth, and none of
    them a path or a document.

    Mutation: add a key to the params dict in ``IpcShellBridge.arm_automation``."""
    tree = ast.parse(_BRIDGE_SRC.read_text(encoding="utf-8"))
    method = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "arm_automation"
    )
    call = next(
        node for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_call"
    )
    params = next(arg for arg in call.args if isinstance(arg, ast.Dict))
    keys = {k.value for k in params.keys if isinstance(k, ast.Constant)}
    assert keys == {"label", "command", "scheduleKind", "schedule"}


# ===========================================================================
# (9) automation.status — what the OPERATING SYSTEM says, asked on demand
# ===========================================================================


def test_the_status_answer_comes_from_the_shell_and_is_never_remembered(tmp_path):
    """Plan §5.6: armed truth lives in the OS. No column stores it, nothing polls,
    nothing checks at startup — so after a restore, a reinstall, or somebody deleting
    the file by hand, the surface says what is actually installed.

    Mutation: return a cached list, or answer from the ``automations`` table — the
    second assertion (a label that has no row) fails."""
    bridge = _FakeArmBridge()
    bridge.armed_labels.append("com.addison.auto.something-else")
    h = build_server(tmp_path, bridge=bridge)  # type: ignore[arg-type]
    try:
        answer = _rpc(h, "automation.status", {}, 1)
        assert answer == {"armed": ["com.addison.auto.something-else"], "supported": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_a_shell_that_cannot_answer_is_not_reported_as_nothing_installed(tmp_path):
    """"Addison could not find out" and "nothing is running" are different answers,
    and collapsing them would tell somebody their automation was off while it ran.

    Mutation: swallow the error and return ``{"armed": [], "supported": False}``."""

    class _Broken(ShellBridgeStubs):
        def list_armed(self) -> dict:
            raise RuntimeError("Addison couldn't finish that just now. Please try again.")

    h = build_server(tmp_path, bridge=_Broken())  # type: ignore[arg-type]
    try:
        answer = _rpc(h, "automation.status", {}, 1)
        assert answer["armed"] == [] and answer["supported"] is False
        assert "try again" in answer["error"].lower()
    finally:
        _shutdown(h.reader, h.thread)


def test_with_no_shell_at_all_the_answer_is_an_honest_nothing(tmp_path):
    """The CLI and every test that wires no bridge: there is nothing to ask, so
    ``supported`` is false and the list is empty — and no error, because nothing
    failed."""
    h = build_server(tmp_path)
    try:
        assert _rpc(h, "automation.status", {}, 1) == {"armed": [], "supported": False}
    finally:
        _shutdown(h.reader, h.thread)
