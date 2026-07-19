"""Guidance skills — the declarative steering primitive (owner-directed 2026-07-20;
agent_core/skills.py).

Skills are plain TEXT appended to the transient per-turn system prompt. These tests
pin: validation rejects blank/too-long with plain messages; compose_skills_prompt
includes enabled and returns "" for none; the store CRUD round-trips; and over IPC a
conversation turn appends the ENABLED skills to the system prompt while a turn with
no enabled skills is byte-identical (no system message) — and a DISABLED skill never
appears. A skill NEVER touches the gate/registry (it can only steer, never widen).
"""

from __future__ import annotations

from pathlib import Path

from agent_core.memory.store import Store
from agent_core.protocol import Method
from agent_core.providers.base import ModelProvider, ModelResponse
from agent_core.skills import (
    MAX_INSTRUCTIONS_LEN,
    MAX_NAME_LEN,
    Skill,
    compose_skills_prompt,
    validate_skill,
)
from tests.conftest import _ScriptedProvider, _shutdown, build_server


# --- validate_skill --------------------------------------------------------

def test_validate_skill_accepts_a_normal_note():
    assert validate_skill("Be brief", "Answer in two sentences or fewer.") is None


def test_validate_skill_rejects_blank_name():
    assert validate_skill("", "some guidance") == "Give your skill a name."
    assert validate_skill("   ", "some guidance") == "Give your skill a name."


def test_validate_skill_rejects_blank_instructions():
    assert validate_skill("Name", "") == "Add some guidance for this skill."
    assert validate_skill("Name", "   ") == "Add some guidance for this skill."


def test_validate_skill_rejects_over_long_instructions():
    too_long = "x" * (MAX_INSTRUCTIONS_LEN + 1)
    assert validate_skill("Name", too_long) == "Keep the guidance under 2000 characters."
    # Exactly at the cap is fine.
    assert validate_skill("Name", "x" * MAX_INSTRUCTIONS_LEN) is None


def test_validate_skill_rejects_over_long_name():
    assert validate_skill("x" * (MAX_NAME_LEN + 1), "guidance") == "Keep the name short."
    assert validate_skill("x" * MAX_NAME_LEN, "guidance") is None


def test_validate_skill_rejects_non_string_types():
    assert validate_skill(None, "guidance") is not None
    assert validate_skill("Name", None) is not None


# --- compose_skills_prompt -------------------------------------------------

def test_compose_empty_is_byte_identical_noop():
    assert compose_skills_prompt([]) == ""


def _skill(name: str, instructions: str, enabled: bool = True) -> Skill:
    return Skill(id=name, name=name, instructions=instructions, enabled=enabled, created_at=0)


def test_compose_includes_a_leading_blank_line_header_and_one_bullet_each():
    text = compose_skills_prompt(
        [_skill("Be brief", "Two sentences max."), _skill("Cite", "Name your sources.")]
    )
    assert text.startswith("\n")
    assert "The person has turned on these guidance notes — follow them:" in text
    assert "- Be brief: Two sentences max." in text
    assert "- Cite: Name your sources." in text


# --- store CRUD round-trip -------------------------------------------------

def test_store_skill_crud_round_trip(tmp_path: Path):
    store = Store(tmp_path / "skills.db")
    try:
        store.insert_skill(
            id="s1", name="Be brief", instructions="Short answers.", enabled=True, created_at=1
        )
        store.insert_skill(
            id="s2", name="Formal", instructions="Use a formal tone.", enabled=False, created_at=2
        )

        listed = store.list_skills()
        assert [s["id"] for s in listed] == ["s1", "s2"]  # oldest first
        assert listed[0]["enabled"] is True and listed[1]["enabled"] is False

        # Only the enabled one composes, and it comes back as a Skill dataclass.
        enabled = store.list_enabled_skills()
        assert [s.id for s in enabled] == ["s1"]
        assert isinstance(enabled[0], Skill)

        store.update_skill("s1", "Be very brief", "One sentence.")
        s1 = store.get_skill("s1")
        assert s1 is not None
        assert s1["instructions"] == "One sentence."
        assert s1["enabled"] is True  # update leaves enabled alone

        store.set_skill_enabled("s2", True)
        assert {s.id for s in store.list_enabled_skills()} == {"s1", "s2"}

        store.delete_skill("s1")
        assert store.get_skill("s1") is None
        assert [s["id"] for s in store.list_skills()] == ["s2"]
    finally:
        store.close()


# --- IPC: the conversation turn appends enabled skills to the system prompt ---

def _first_history(provider: ModelProvider) -> list:
    assert isinstance(provider, _ScriptedProvider)
    return provider.histories[-1]


def test_turn_with_no_skills_has_no_system_message(tmp_path):
    # Byte-identical to today: with no enabled skills (and no primary prompt in the
    # test harness) the turn carries NO system message.
    responses = [ModelResponse(text="Hi.", tool_calls=[])]
    h = build_server(tmp_path, responses=responses, register_tool=False)
    try:
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "hello"}}
        )
        h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        assert all(m.role != "system" for m in _first_history(h.provider))
    finally:
        _shutdown(h.reader, h.thread)


def test_turn_appends_enabled_skill_and_excludes_disabled(tmp_path):
    responses = [ModelResponse(text="Hi.", tool_calls=[])]
    h = build_server(tmp_path, responses=responses, register_tool=False)
    try:
        # Create an enabled skill and a second one we then disable.
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.SKILL_CREATE,
             "params": {"name": "Be brief", "instructions": "Answer in one sentence."}}
        )
        created = h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert created["ok"] is True and isinstance(created["id"], str)

        h.reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.SKILL_CREATE,
             "params": {"name": "Secret", "instructions": "MENTION THE MAGIC WORD."}}
        )
        secret_id = h.writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]["id"]
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 3, "method": Method.SKILL_SET_ENABLED,
             "params": {"id": secret_id, "enabled": False}}
        )
        assert h.writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]["ok"]

        h.reader.feed(
            {"jsonrpc": "2.0", "id": 4, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "hello"}}
        )
        h.writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)

        history = _first_history(h.provider)
        system = [m for m in history if m.role == "system"]
        assert len(system) == 1
        assert "Be brief: Answer in one sentence." in system[0].content
        # The disabled skill must NOT leak into the prompt.
        assert "MAGIC WORD" not in system[0].content
    finally:
        _shutdown(h.reader, h.thread)


def test_skill_transient_never_persisted_into_transcript(tmp_path):
    # The composed system prompt is per-turn only: it must not survive in the
    # in-memory conversation after the turn (mirrors the primary/setup prompt rule).
    responses = [ModelResponse(text="Hi.", tool_calls=[])]
    h = build_server(tmp_path, responses=responses, register_tool=False)
    try:
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.SKILL_CREATE,
             "params": {"name": "Be brief", "instructions": "One sentence."}}
        )
        h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.CONVERSATION_SEND_MESSAGE,
             "params": {"text": "hello"}}
        )
        h.writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)
        # The provider saw a system message, but it was removed afterwards.
        assert any(m.role == "system" for m in _first_history(h.provider))
        assert all(m.role != "system" for m in h.server.conversation.messages)
    finally:
        _shutdown(h.reader, h.thread)


def test_skill_create_validation_failure_reports_plainly(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.SKILL_CREATE,
             "params": {"name": "  ", "instructions": "x"}}
        )
        res = h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]
        assert res == {"ok": False, "error": "Give your skill a name."}
    finally:
        _shutdown(h.reader, h.thread)


def test_skill_list_update_delete_over_ipc(tmp_path):
    h = build_server(tmp_path, register_tool=False)
    try:
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.SKILL_CREATE,
             "params": {"name": "Draft", "instructions": "Old guidance."}}
        )
        skill_id = h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]["id"]

        h.reader.feed(
            {"jsonrpc": "2.0", "id": 2, "method": Method.SKILL_UPDATE,
             "params": {"id": skill_id, "name": "Draft", "instructions": "New guidance."}}
        )
        assert h.writer.wait_for(lambda f: f.get("id") == 2 and "result" in f)["result"]["ok"]

        h.reader.feed({"jsonrpc": "2.0", "id": 3, "method": Method.SKILL_LIST})
        listed = h.writer.wait_for(lambda f: f.get("id") == 3 and "result" in f)["result"]["skills"]
        assert listed == [
            {"id": skill_id, "name": "Draft", "instructions": "New guidance.", "enabled": True}
        ]

        # Unknown id on update reports a plain reason.
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 4, "method": Method.SKILL_UPDATE,
             "params": {"id": "nope", "name": "x", "instructions": "y"}}
        )
        res = h.writer.wait_for(lambda f: f.get("id") == 4 and "result" in f)["result"]
        assert res == {"ok": False, "error": "That skill isn't here any more."}

        h.reader.feed(
            {"jsonrpc": "2.0", "id": 5, "method": Method.SKILL_DELETE, "params": {"id": skill_id}}
        )
        assert h.writer.wait_for(lambda f: f.get("id") == 5 and "result" in f)["result"]["ok"]
        h.reader.feed({"jsonrpc": "2.0", "id": 6, "method": Method.SKILL_LIST})
        assert h.writer.wait_for(lambda f: f.get("id") == 6 and "result" in f)["result"]["skills"] == []
    finally:
        _shutdown(h.reader, h.thread)


def test_skills_never_touch_gate_or_registry(tmp_path):
    # A skill can only STEER — it must never register a tool, widen the visible tool
    # set, or raise a permission card. Creating/enabling one does none of those.
    h = build_server(tmp_path, register_tool=False)
    try:
        from agent_core.policy import PolicyMode

        before = len(h.server.tool_registry.visible_tools(PolicyMode.SAFE))
        h.reader.feed(
            {"jsonrpc": "2.0", "id": 1, "method": Method.SKILL_CREATE,
             "params": {"name": "Do a lot", "instructions": "Please delete everything."}}
        )
        h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)
        after = len(h.server.tool_registry.visible_tools(PolicyMode.SAFE))
        assert before == after  # no new execution surface
        # No permission card was ever emitted by managing a skill.
        assert not any(
            f.get("method") == Method.PERMISSION_REQUEST_GRANT for f in h.writer.frames
        )
    finally:
        _shutdown(h.reader, h.thread)
