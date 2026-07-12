"""Profiles — audience-adaptive surface over one shared engine (engineering-spec §4.7).

A Profile reshapes *which tools are registered*, *which onboarding path runs*, and
*which frontend surfaces are shown*. It is configuration, **not** a security
boundary: switching profiles never bypasses the permission gate, the
undo-at-registration check, key isolation, or the no-arbitrary-shell rule (spec §8.7).

STATUS: scaffold for build step 11 (the LAST v1 step, spec §11). The Profile config
below is real — it's just data — but wiring it into onboarding and the frontend
feature flags is deferred to step 11. Until then the app behaves as the Simple
profile, which is exactly what build steps 1-10 produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProfileId(str, Enum):
    SIMPLE = "simple"        # default — the non-technical personas (design-doc §5)
    DEVELOPER = "developer"  # opt-in — technical users


# The v1 tool set (spec §4.2). The Simple profile exposes exactly these.
_V1_TOOL_IDS = [
    "web_search",
    "read_file",
    "read_clipboard",
    "calculator",
    "save_file",
    "draft_message",
    "open_link",
]


@dataclass
class Profile:
    id: ProfileId
    tool_ids: list[str]                  # which registered tools this profile exposes
    onboarding: str                      # "setup_assistant" | "byok_first"
    expose_routine_plan: bool = False    # Developer: read-only view of the declarative plan (§6.5)
    headless_cli: bool = False           # Developer: expose the Agent Core JSON-RPC entry point
    raw_diagnostics: bool = False        # Developer: real errors/logs vs. translated messages
    allow_advanced_tools: bool = False   # Developer: permit opt-in higher-risk tools (still gated + undoable)


SIMPLE = Profile(
    id=ProfileId.SIMPLE,
    tool_ids=list(_V1_TOOL_IDS),
    onboarding="setup_assistant",
)

DEVELOPER = Profile(
    id=ProfileId.DEVELOPER,
    # Same gated v1 set for now; opt-in higher-risk tools are added here at step 11,
    # each still routed through the permission gate + undo (never a safety bypass).
    tool_ids=list(_V1_TOOL_IDS),
    onboarding="byok_first",
    expose_routine_plan=True,
    headless_cli=True,
    raw_diagnostics=True,
    allow_advanced_tools=True,
)

_PROFILES = {ProfileId.SIMPLE: SIMPLE, ProfileId.DEVELOPER: DEVELOPER}

DEFAULT_PROFILE_ID = ProfileId.SIMPLE


def get_profile(profile_id: ProfileId) -> Profile:
    return _PROFILES[profile_id]


def resolve_active_profile(store=None) -> Profile:
    """Return the active profile, defaulting to SIMPLE.

    In the finished product this reads `app_settings.active_profile` (spec §3);
    in the current scaffold (pre-step-11) there is no persisted value, so it
    always resolves to SIMPLE."""
    # TODO(step 11): return get_profile(store.get_setting("active_profile", DEFAULT_PROFILE_ID)).
    return SIMPLE
