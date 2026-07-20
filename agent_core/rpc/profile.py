"""profile.* handlers — report the active §4.7 profile (and the policy mode it
derives) and switch it live (engineering-spec §7, §4.7; policy.py)."""

from __future__ import annotations

from agent_core.policy import mode_for_profile
from agent_core.profiles import DEVELOPER, SIMPLE, ProfileId, get_profile
from agent_core.rpc.base import ServerContext
from agent_core.rpc.constants import _SERVER_ERROR, _UNKNOWN_PROFILE_MESSAGE


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
            "profiles": [
                {"id": p.id.value, "label": p.label, "description": p.description}
                for p in (SIMPLE, DEVELOPER)
            ],
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
        derives the policy mode — Simple=SAFE, Developer=OPEN — which reshapes the
        permission gate (OPEN prompts only for destructive actions) and the visible
        tool set (OPEN surfaces run_command). The two GLOBAL invariants never move:
        keys stay keychain-only and never reach the webview/SQLite, and there is no
        scheduling in either mode. Switching modes is always allowed; dev-created
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
        # Mode is derived live from _active_profile (policy.py) — the switch takes
        # effect immediately and needs no per-mode cache to refresh: the orchestrator
        # reads visible_tools(mode) per turn and the gate takes mode per call. Return
        # the new mode for the frontend (next PR).
        self._respond(request_id, {"ok": True, "mode": mode_for_profile(profile).value})
