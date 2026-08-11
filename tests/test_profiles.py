"""Profiles are a surface layer, not a security boundary (spec §4.7, §8.7).

The Simple profile is the default and must expose exactly the v1 tool set.

Since the mode-scoped model (owner decision 2026-07-19) the Developer profile
DOES bring real new capability — its OPEN mode surfaces ``run_command`` and the
automation tools. What these tests pin is WHERE that widening is allowed to
happen: not in the Profile dataclass, which carries the same ``tool_ids`` either
way, but in mode-scoped registration (``main.build_registry``) behind the
registry's SAFE/OPEN filtered view. Those tools' visibility and dispatch rules
belong to tests/test_tool_registry.py and tests/test_policy_modes.py; a profile
still never widens anything by itself.

``read_project_file`` / ``write_project_file`` were on the Developer side of that
line until 2026-08-11 and are now in the SAFE set below — the owner decided
Simple's inability to change an existing file was a bug, and the two tools
register with no flags (docs/SAFETY.md owns the decision; the card, confinement
and undo are what keep them safe there, tests/test_workspace_trust.py §5).
"""

from agent_core.main import build_registry
from agent_core.profiles import DEVELOPER, SIMPLE, ProfileId, resolve_active_profile

_V1_TOOLS = [
    "calculator",
    "draft_message",
    "open_link",
    "read_clipboard",
    "read_file",
    "read_project_file",
    "read_web_page",
    "save_file",
    "snapshot_now",
    "web_search",
    "write_project_file",
]


def test_default_profile_is_simple():
    assert resolve_active_profile().id is ProfileId.SIMPLE


def test_simple_profile_registers_exactly_v1_tool_set():
    ids = sorted(t.id for t in build_registry(SIMPLE).list_for_model())
    assert ids == _V1_TOOLS


def test_build_registry_defaults_to_simple():
    # No-arg call still works and matches the Simple profile (backward compatible).
    assert sorted(t.id for t in build_registry().list_for_model()) == _V1_TOOLS


def test_developer_profile_dataclass_carries_no_extra_tools_of_its_own():
    """Developer's extra capability comes from its MODE, never from this dataclass.

    Developer unlocks surfaces here (CLI, raw diagnostics, plan view) and its
    ``tool_ids`` stay identical to Simple's. That is not the old "surface only, no
    new capability" claim — Developer maps to OPEN, and OPEN really does surface
    ``run_command`` and the automation tools. But they arrive by
    ``build_registry`` registering them ``dev_only``/``open_only``, so the registry's
    SAFE view and ``refuse_if_dev_only_outside_open`` are what hold the line
    (tests/test_tool_registry.py, tests/test_policy_modes.py).

    Keeping the two ``tool_ids`` equal is what stops a SECOND, unfiltered route to
    the same widening: a profile that could hand itself a tool list would bypass
    the mode check entirely, and no one reading policy.py would see it happen."""
    assert DEVELOPER.headless_cli
    assert DEVELOPER.raw_diagnostics
    assert DEVELOPER.expose_routine_plan
    assert set(DEVELOPER.tool_ids) == set(SIMPLE.tool_ids)
