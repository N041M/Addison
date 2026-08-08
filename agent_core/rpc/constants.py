"""Shared JSON-RPC error codes and plain-language messages (CLAUDE.md: no jargon).

Split out of ``main.py`` so the composition root AND the handler mixins can import
them without a cycle. ``main`` re-exports the two message constants tests import by
name (``_BYOK_ONBOARDING_MESSAGE`` / ``_UNKNOWN_PROFILE_MESSAGE``) so
``from agent_core.main import ...`` keeps working.
"""

from __future__ import annotations

from agent_core.policy import PolicyMode

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

# --- PRIMARY key status (§4.6) ------------------------------------------------
# THREE outcomes, not two — and they now live in ONE vocabulary,
# ``agent_core.secret_presence.SecretPresence`` (present | absent | unknown), shared
# with the stored ``provider_config.secret_presence`` column. There used to be a
# second, parallel set of words here (ready/missing/unreadable) meaning exactly the
# same three things, which is how a rule gets restated and then drifts. The relay
# rule itself is ``may_reach_setup_relay`` and lives nowhere else.
#
# Said instead of running the turn when the read failed. It names the way out
# (Allow, or re-add) and stays true whichever of the two happened. "your computer's
# keychain" matches the wording already used on the connect card (rpc/providers.py).
_KEY_UNREADABLE_MESSAGE = (
    "Addison couldn't read your saved key from your computer's keychain just now. "
    "If your computer asks for permission, choose Allow — or add the key again in "
    "Settings."
)
_UNKNOWN_PROFILE_MESSAGE = "That profile isn't available."

# --- Artifact unavailability (owner decision 2026-08-06) ----------------------
# A routine/widget created in OPEN used to be FILTERED OUT of the Simple lists, so
# switching Developer -> Simple made the person's own work silently disappear
# ("where did my stuff go?"). It is now LISTED and visibly disabled, carrying the
# same sentence dispatch already refuses it with. docs/SAFETY.md owns the rule.
#
# The marker is a REASON, never a boolean: a later cause (a tool that becomes
# dev-only in an app update, say) is a new slug in this vocabulary and needs no
# schema change on either side of the wire.
_UNAVAILABLE_DEV_ABILITIES = "developer_abilities"
_ROUTINE_DEV_ABILITIES_MESSAGE = (
    "That routine uses developer abilities, so it's waiting in Developer profile."
)
_WIDGET_DEV_ABILITIES_MESSAGE = (
    "That widget uses developer abilities, so it's waiting in Developer profile."
)
# An automation's payload is a shell command, so EVERY automation needs Developer —
# there is no such thing as one a Simple profile could arm. That uniformity is why
# its caller passes a decided `True` rather than asking the row anything: with no
# per-row question there is no stamp to be tempted into reading (step 8 phase 4;
# the routines half above is the cautionary entry in KNOWN-GAPS).
_AUTOMATION_DEV_ABILITIES_MESSAGE = (
    "That automation runs a command, so it's waiting in Developer profile."
)


def _unavailable_marker(mode: PolicyMode, needs_dev: bool, message: str) -> dict | None:
    """The DISPLAY-ONLY unavailability marker for one list row, or None.

    **This is not the enforcement, and must never become it.** What actually stops
    an artifact that needs developer abilities in SAFE is dispatch:
    ``routine.run``'s refusal (``rpc/routines.py``), the routine engine's per-step
    ``dev_only`` check, and ``widget.run``'s SAFE refusal (``rpc/widgets.py``).
    This function only decides what a list SAYS. If the two ever disagree — a row
    listed without a marker, say — **dispatch wins**: the absence of a marker is
    not a permission, and a stale or hand-edited frontend gets the same refusal as
    an honest one.

    ``needs_dev`` is a DECIDED BOOLEAN, and taking it rather than the row's
    ``created_in_mode`` is the point. The stamp records where an artifact was
    born; the question here is what it ASKS FOR, and the two part company for
    every artifact that is perfectly usable in Simple but happened to be made
    while Developer was active. Callers derive it from the artifact itself —
    ``widgets.widget_uses_dev_abilities`` — so a wrong answer has to be written
    somewhere it can be read, rather than inherited from a stamp.

    Returns ``{"reason": <slug>, "message": <plain sentence>}``; callers omit the
    key entirely when this is None, so an available row's shape is unchanged.
    """
    if mode is not PolicyMode.SAFE or not needs_dev:
        return None
    return {"reason": _UNAVAILABLE_DEV_ABILITIES, "message": message}

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
