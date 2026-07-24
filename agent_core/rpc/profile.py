"""profile.* handlers — report the active §4.7 profile (and the policy mode it
derives) and switch it live (engineering-spec §7, §4.7; policy.py)."""

from __future__ import annotations

from agent_core.policy import mode_for_profile
from agent_core.profiles import CUSTOM, DEVELOPER, SIMPLE, Profile, ProfileId, get_profile
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _SERVER_ERROR, _UNKNOWN_PROFILE_MESSAGE


def _profile_entry(profile: Profile) -> dict:
    """One selector option. The base shape is byte-stable across every profile;
    ``advanced`` is added ONLY when the profile carries it (Custom), so the
    Simple/Developer entries stay exactly as they serialized before (D4)."""
    entry: dict = {
        "id": profile.id.value,
        "label": profile.label,
        "description": profile.description,
    }
    if profile.advanced:
        entry["advanced"] = True
    return entry


class ProfileMixin(ServerContext):
    def _profile_get(self) -> dict:
        """The active profile, the selector's option list, and the feature flags
        for the ACTIVE profile. Flags are pure surface signals the frontend uses to
        show/hide Developer-only affordances — they never gate tool execution (§8.7)."""
        active = self._active_profile or SIMPLE
        return {
            "activeProfile": active.id.value,
            # The policy mode this profile runs under ('safe' | 'open'), derived 1:1
            # from the profile (policy.py). Consumed by the next (frontend) PR.
            "mode": mode_for_profile(active).value,
            # SIMPLE/DEVELOPER entries keep their exact serialized shape (no new
            # keys — the drift + fixture tests pin those bytes); the CUSTOM entry
            # ALONE carries "advanced": true, which the frontend uses to render it
            # behind an Advanced disclosure with a two-step confirm (D4).
            "profiles": [_profile_entry(p) for p in (SIMPLE, DEVELOPER, CUSTOM)],
            "flags": {
                "exposeRoutinePlan": active.expose_routine_plan,
                "rawDiagnostics": active.raw_diagnostics,
                "headlessCli": active.headless_cli,
                "byokFirstOnboarding": active.onboarding == "byok_first",
            },
        }

    def _handle_profile_set(self, params: dict, request_id) -> None:
        """Persist the chosen profile and re-resolve it for the running server so the
        switch takes effect immediately (no restart). An unknown id is refused plainly.

        Mode-scoped safety (owner decision 2026-07-19, policy.py): the profile also
        derives the policy mode — Simple=SAFE, Developer AND Custom=OPEN — which
        reshapes the permission gate (OPEN prompts only for destructive actions) and
        the visible tool set (OPEN surfaces run_command). Custom additionally applies
        its two prompting guards over the OPEN gate (guards.*), which can only change
        how often it asks — never a GLOBAL floor. The GLOBAL invariants never move:
        keys stay keychain-only and never reach the webview/SQLite, and there is no
        scheduling in any mode. Switching profiles is always allowed; dev-created
        routines/widgets simply hide in SAFE and return in OPEN."""
        try:
            profile = get_profile(ProfileId(params.get("profileId")))
        except ValueError:
            self._respond_error(request_id, _SERVER_ERROR, _UNKNOWN_PROFILE_MESSAGE)
            return
        # Hook H1 (G3): a restore point holding the PRE-switch profile, taken only
        # after the guard above so an unknown id can never mint one. A profile
        # switch is the sweeping change the amendment's motivating story turns on,
        # but it is also one the person can simply redo — so a failed capture
        # proceeds with the sticky warning rather than blocking the switch.
        self._snapshot_auto("mode_switch")
        self.store.set_setting("active_profile", profile.id.value)
        self._active_profile = profile
        # A profile change is the same posture event as a G3 restore (the
        # revoke_all docstring's own principle): the new profile — or its guard
        # posture — may be STRICTER than the session's accumulated grants, so
        # leaving them in place would keep the session wider than the profile the
        # user just chose. This also clears the Custom session-destructive grants,
        # so a "Ask once" approval never survives a switch away from Custom [R2].
        try:
            self.permission_gate.revoke_all()
            self.permission_gate.clear_denials()
        except Exception:
            pass
        # Mode is derived live from _active_profile (policy.py) — the switch takes
        # effect immediately and needs no per-mode cache to refresh: the orchestrator
        # reads visible_tools(mode) per turn and the gate takes mode per call. Return
        # the new mode for the frontend (next PR).
        self._respond(request_id, {"ok": True, "mode": mode_for_profile(profile).value})
