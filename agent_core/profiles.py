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
    # Plain-language selector copy (design-doc §7.11). ``label`` is the short option
    # name; ``description`` is one honest sentence — and it MUST keep saying that the
    # safety rules are identical across profiles (§8.7): Developer changes the surface,
    # never the gate/undo/key/no-shell invariants.
    label: str = ""
    description: str = ""
    expose_routine_plan: bool = False    # Developer: read-only view of the declarative plan (§6.5)
    headless_cli: bool = False           # Developer: expose the Agent Core JSON-RPC entry point
    raw_diagnostics: bool = False        # Developer: real errors/logs vs. translated messages
    allow_advanced_tools: bool = False   # Developer: permit opt-in higher-risk tools (still gated + undoable)


SIMPLE = Profile(
    id=ProfileId.SIMPLE,
    tool_ids=list(_V1_TOOL_IDS),
    onboarding="setup_assistant",
    label="Simple",
    description="Simple — the everyday Addison.",
)

DEVELOPER = Profile(
    id=ProfileId.DEVELOPER,
    # Same gated v1 set for now; opt-in higher-risk tools are added here at step 11,
    # each still routed through the permission gate + undo (never a safety bypass).
    tool_ids=list(_V1_TOOL_IDS),
    onboarding="byok_first",
    label="Developer",
    # Honest per §8.7: extra *visibility*, identical *safety*.
    description="Developer — extra visibility for technical users. Same safety rules.",
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

    Reads ``app_settings.active_profile`` (spec §3, §4.7) when a ``store`` is
    given; with no store (CLI/dev/tests) or a missing/unknown persisted value it
    resolves to SIMPLE. This is a *surface* choice only — whichever profile comes
    back, the permission gate, undo-at-registration check, key isolation and
    no-arbitrary-shell rule are identical (§8.7)."""
    if store is None:
        return SIMPLE
    raw = store.get_setting("active_profile", DEFAULT_PROFILE_ID.value)
    try:
        return get_profile(ProfileId(raw))
    except ValueError:
        # An unknown/garbage persisted value never escalates surface — SIMPLE.
        return SIMPLE
