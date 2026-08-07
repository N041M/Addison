"""workspace.* handlers — the OPEN-mode coding harness's trust boundary (step 5).

A "trusted folder" lets Addison read and edit files inside it WITHOUT a card for
every change — each edit is still logged and undoable, and commands it runs still
ask every time (owner decision 2026-07-24; contract §3/§4). Outside a trusted
folder the typed file tools are hard-refused before they run (confinement, D3);
``run_command`` is never affected either way (its ``affected_path`` is None).

This module owns:
  * the RPC (``grantTrust`` / ``revokeTrust`` / ``list``), the sole camelCase
    mapper for its namespace at the wire boundary;
  * ``is_trusted`` / ``_is_trusted_path`` — the ONE resolver both grant time (RPC)
    and authorize time (orchestrator/engine/widgets, via a wired ``trust_check``)
    read, so the two can never drift.

The FLOOR beats a root (D4): a path is trusted iff it sits under a granted root
AND passes ``policy.workspace_trust_allows`` (i.e. is not Addison's own data dir or
under it). Match-a-root THEN floor, so a root someone planted over the data dir
still fails the floor. ``grantTrust`` refuses the data dir at the door for the same
reason. Trust rows are EXCLUDED from snapshots (scope.py, D2): standing consent,
never resurrected by a restore.
"""

from __future__ import annotations

import os
import time

from agent_core.policy import (
    TRUST_REFUSAL_AUTOMATION,
    path_is_within,
    trust_refusal,
    workspace_trust_allows,
)
from agent_core.rpc.base import ServerContext
from agent_core.tools.base import call_is_forbidden

# Frozen plain-language copy (D6, F2). The frontend asserts these bytes.
_GRANT_DATA_DIR_REFUSAL = (
    "That folder holds Addison's own memory, so Addison always asks there. "
    "Pick a project folder instead."
)
# The step-8 fence's own sentence (plan §5.5). One sentence for both refusals was
# the first draft, and it told a person picking ~/Library/LaunchAgents that the
# folder holds Addison's memory — false, and a false reason teaches people that
# refusals are boilerplate. ``policy.trust_refusal`` says which group refused;
# ``~`` still gets the memory sentence (protected wins on a path that offends
# both), so no previously-refused folder changed its wording.
_GRANT_AUTOMATION_DIR_REFUSAL = (
    "That folder is where this computer keeps jobs it runs on a schedule, so "
    "Addison never trusts it. Pick a project folder instead."
)
_GRANT_NOT_A_FOLDER = "That folder isn't there, so Addison can't trust it."
_GRANT_NEEDS_ABSOLUTE = "Addison needs the full path to a folder to trust it."


def is_trusted(resolved_path: str, trusted_roots: list[str], data_dir: str) -> bool:
    """Pure predicate: is ``resolved_path`` (already realpath'd) genuinely trusted?

    Match-a-root THEN floor: the path must sit under one of ``trusted_roots``
    (stored canonical, so realpath-vs-realpath) AND pass the data-dir floor. A
    planted root over the data dir therefore never confines anything (the floor
    wins). Store-free by construction so the gate stays store-free (F6) — the caller
    supplies the roots and the data dir."""
    if not any(path_is_within(resolved_path, root) for root in trusted_roots):
        return False
    return workspace_trust_allows(resolved_path, data_dir)


class WorkspaceMixin(ServerContext):
    # --- the shared resolver (grant time AND authorize time) ----------------
    def _data_dir(self) -> str:
        """The live DB's parent — the value ``workspace_trust_allows`` protects.
        Derived from the running store's path (never a re-derivation), falling back
        to policy's derivation only when no db_path was wired (CLI-ish tests)."""
        if self._db_path is not None:
            return str(self._db_path.parent)
        from agent_core.policy import _derived_data_dir

        return _derived_data_dir()

    def _is_trusted_path(self, resolved_path: str) -> bool:
        """Whether a RESOLVED path may be touched by a path-bounded tool right now:
        under a currently-trusted root AND past the floor. Wired into the
        orchestrator / routine engine / widget rail as ``trust_check`` so confinement
        (D3) and the gate's ``trusted`` bool read the exact same answer."""
        roots = [row["root"] for row in self.store.list_workspace_trust()]
        return is_trusted(resolved_path, roots, self._data_dir())

    def _trusted_roots(self) -> list[str]:
        """Every currently-trusted root, canonical. Read at the moment of use, never
        cached on a turn: revoking trust must take effect on the next command, not
        the next conversation. Feeds the seatbelt profile's write-allowlist (step
        5.5, item 2) — which is what finally makes workspace trust govern the
        shell, rather than only the careful path-bounded tools."""
        return [row["root"] for row in self.store.list_workspace_trust()]

    def _is_forbidden_call(self, tool, args) -> str | None:
        """The hardline denylist (step 5.5, item 3), bound to the LIVE data dir.

        Wired into the orchestrator / routine engine as ``forbidden_check`` and
        called directly by the widget rail, so all three sites read one answer —
        the same discipline ``_is_trusted_path`` exists for. Without this binding
        the predicate re-derived the data dir from the environment and would have
        protected the default store while the running one went unguarded."""
        return call_is_forbidden(tool, args, self._data_dir())

    # --- RPC ----------------------------------------------------------------
    def _workspace_list(self) -> dict:
        """workspace.list -> every trusted folder, newest first."""
        self._ensure_built()
        return {
            "folders": [
                {"directory": row["root"], "grantedAt": row["granted_at"]}
                for row in self.store.list_workspace_trust()
            ]
        }

    def _workspace_pick_directory(self) -> dict:
        """workspace.pickDirectory -> {directory: str | null}. Relays the shell's
        native folder picker so the "Trust a folder" flow reaches a real OS dialog;
        the frontend then calls grantTrust with the chosen path. A cancelled picker
        (or no shell wired) returns ``{"directory": null}`` — not an error, just no
        choice."""
        self._ensure_built()
        if self._shell_bridge is None:
            return {"directory": None}
        try:
            directory = self._shell_bridge.pick_directory()
        except RuntimeError:
            return {"directory": None}
        return {"directory": directory or None}

    def _workspace_grant(self, params: dict) -> dict:
        """workspace.grantTrust {directory} -> {ok, directory} | {ok:false, error}.

        Validates the folder is an absolute, existing directory; CANONICALIZES it
        (realpath) so the stored root matches what the confinement check resolves;
        REFUSES the data dir (or an ancestor/descendant of it) at the door — the
        floor, not a strippable warning; snapshots-and-proceeds
        (``_snapshot_auto("workspace_trust")``, provider_connect class — trust is
        trivially re-grantable, so a capture failure only warns), then stores it."""
        self._ensure_built()
        directory = params.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            return {"ok": False, "error": _GRANT_NEEDS_ABSOLUTE}
        expanded = os.path.expanduser(directory.strip())
        if not os.path.isabs(expanded):
            return {"ok": False, "error": _GRANT_NEEDS_ABSOLUTE}
        if not os.path.isdir(expanded):
            return {"ok": False, "error": _GRANT_NOT_A_FOLDER}
        root = os.path.realpath(expanded)
        # The floor: neither Addison's own data dir (§6.6) nor an OS-automation
        # directory (step-8 plan §5.5) can ever be trusted. Same predicate the
        # confinement path applies, so grant and touch agree — asked here through
        # ``trust_refusal`` only so the sentence can name the true reason.
        refusal = trust_refusal(root, self._data_dir())
        if refusal is not None:
            if refusal == TRUST_REFUSAL_AUTOMATION:
                return {"ok": False, "error": _GRANT_AUTOMATION_DIR_REFUSAL}
            return {"ok": False, "error": _GRANT_DATA_DIR_REFUSAL}
        # Risky change -> a restore point first, but trust is trivially re-grantable,
        # so a capture failure warns (sticky) rather than refusing the grant.
        self._snapshot_auto("workspace_trust")
        self.store.insert_workspace_trust(root=root, granted_at=int(time.time()))
        return {"ok": True, "directory": root}

    def _workspace_revoke(self, params: dict) -> dict:
        """workspace.revokeTrust {directory} -> {ok}. Revoking only tightens, so no
        snapshot. Canonicalizes so a differently-spelled path still matches the
        stored root."""
        self._ensure_built()
        directory = params.get("directory")
        if not isinstance(directory, str) or not directory.strip():
            return {"ok": False, "error": _GRANT_NEEDS_ABSOLUTE}
        root = os.path.realpath(os.path.expanduser(directory.strip()))
        self.store.delete_workspace_trust(root)
        return {"ok": True}
