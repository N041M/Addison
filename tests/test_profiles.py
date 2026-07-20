"""Profiles are a surface layer, not a security boundary (spec §4.7, §8.7).

The Simple profile is the default and must expose exactly the v1 tool set; the
Developer profile flips *surface* flags without, in v1, widening the tool set
beyond the same gated set (advanced tools are opt-in at step 11).
"""

from agent_core.main import build_registry
from agent_core.profiles import DEVELOPER, SIMPLE, ProfileId, resolve_active_profile

_V1_TOOLS = [
    "calculator",
    "draft_message",
    "open_link",
    "read_clipboard",
    "read_file",
    "read_web_page",
    "save_file",
    "web_search",
]


def test_default_profile_is_simple():
    assert resolve_active_profile().id is ProfileId.SIMPLE


def test_simple_profile_registers_exactly_v1_tool_set():
    ids = sorted(t.id for t in build_registry(SIMPLE).list_for_model())
    assert ids == _V1_TOOLS


def test_build_registry_defaults_to_simple():
    # No-arg call still works and matches the Simple profile (backward compatible).
    assert sorted(t.id for t in build_registry().list_for_model()) == _V1_TOOLS


def test_developer_profile_is_surface_only_not_new_capability():
    # Developer unlocks surfaces (CLI, raw diagnostics, plan view) — not, in v1,
    # a wider tool set than Simple. Same tools, same gate.
    assert DEVELOPER.headless_cli
    assert DEVELOPER.raw_diagnostics
    assert DEVELOPER.expose_routine_plan
    assert set(DEVELOPER.tool_ids) == set(SIMPLE.tool_ids)
