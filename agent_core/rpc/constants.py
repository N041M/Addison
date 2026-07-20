"""Shared JSON-RPC error codes and plain-language messages (CLAUDE.md: no jargon).

Split out of ``main.py`` so the composition root AND the handler mixins can import
them without a cycle. ``main`` re-exports the two message constants tests import by
name (``_BYOK_ONBOARDING_MESSAGE`` / ``_UNKNOWN_PROFILE_MESSAGE``) so
``from agent_core.main import ...`` keeps working.
"""

from __future__ import annotations

# JSON-RPC error codes. -32601 is the reserved "method not found"; the -32000
# band is the "server error" range we use for provider/tool/not-built failures,
# each carrying a plain-language message (never a stack trace).
_METHOD_NOT_FOUND = -32601
_SERVER_ERROR = -32000

_NOT_BUILT_MESSAGE = "This isn't built yet."
# Plain-language model-picker refusals (§4.1.1; CLAUDE.md: no jargon).
_MODEL_UNAVAILABLE_MESSAGE = "That model option isn't available."
_EFFORT_UNAVAILABLE_MESSAGE = "That answer-style isn't available for this model."
_GENERIC_TURN_ERROR = (
    "Addison couldn't finish that just now. Check your internet connection and "
    "that your API key is still valid, then try again."
)

# Local-setup (§4.1.2) plain-language messages. Addison does NOT install Ollama
# in v1 — it points the user at doing that themselves.
_OLLAMA_NOT_INSTALLED_MESSAGE = (
    "Ollama isn't running on this computer. Install it from ollama.com (or start "
    "it if it's already installed), then try again — Addison can't install it for you."
)
_LOCAL_SETUP_BUSY_MESSAGE = (
    "Addison is already setting up a model. Let that one finish before starting another."
)
# §4.7 Developer profile is BYOK-first: with no key it asks the user to add their own
# rather than routing to the Setup Assistant relay (which is the Simple onboarding).
_BYOK_ONBOARDING_MESSAGE = (
    "No API key is set up yet. Add your Anthropic API key in Settings."
)
_UNKNOWN_PROFILE_MESSAGE = "That profile isn't available."

# G3: the store could not be opened. Plain, actionable, no stack trace. It names
# Restore because Restore genuinely works in this state — snapshot.list and
# snapshot.restoreLastWorking are exempt from the build-failure short-circuit and
# are served store-free from the sidecar files (main.py, contract §6.4c/§6.5).
_STORE_UNAVAILABLE_MESSAGE = (
    "Addison couldn't open its settings file. Restart Addison — if it happens "
    "again, use Restore in Settings to go back to your last working setup."
)
# The cold-start rebuild succeeded: the damaged file was renamed aside (never
# deleted) and a fresh one was built from the last working setup on disk.
_REBUILT_MESSAGE = (
    "Addison's settings file was damaged, so I rebuilt it from your last working "
    "setup. Your chats and saved keys are untouched."
)
# ...and when there is nothing on disk to rebuild from. An honest failure beats a
# silent one — this is the one place the floor can genuinely not deliver.
_NOTHING_TO_REBUILD_FROM = (
    "Addison couldn't open its settings file, and there's no saved restore point "
    "to rebuild from."
)
