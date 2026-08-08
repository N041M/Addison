"""workspace.* handlers — the OPEN-mode coding harness's trust boundary (step 5).

A "trusted folder" lets Addison read and edit files inside it WITHOUT a card for
every change — each edit is still logged and undoable, and commands it runs still
ask every time (owner decision 2026-07-24; contract §3/§4). Outside a trusted
folder the typed file tools are hard-refused before they run (confinement, D3);
``run_command`` is never affected either way (its ``affected_path`` is None).

This module owns:
  * the RPC (``grantTrust`` / ``revokeTrust`` / ``list`` / ``pickDirectory``, plus the
    review surface's read paths ``listDirectory`` / ``readFile``), the sole camelCase
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
    PolicyMode,
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

# --- the review surface's read paths (Phase-3 plan Build §1) ----------------
# Frozen plain-language copy, same rule as the grant refusals above. Each says what
# is true and what to do, and none of them names a mechanism: "the Developer
# profile" is a thing on a settings screen, "OPEN mode" is not.
_BROWSE_NEEDS_DEVELOPER = (
    "Looking inside your folders is part of the Developer profile. Switch to it in "
    "Settings to browse them here."
)
_BROWSE_NEEDS_A_FOLDER = "Addison needs the full path to the folder to look inside it."
_BROWSE_NEEDS_A_FILE = "Addison needs the full path to the file to show it."
# The ONE sentence for every not-inside-a-trusted-folder answer, browse and read
# alike — including a shortcut that leads out of one. Naming the shortcut would be a
# worse sentence, not a better one: the person is looking at a name in a list, and
# what they need to know is that this is outside what they trusted.
_BROWSE_NOT_TRUSTED = (
    "That's outside the folders you've trusted, so Addison won't look there. Trust "
    "the folder first if you want Addison to see inside it."
)
_BROWSE_NO_SHELL = "Addison can't look at your files just now. Please try again."


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

    # --- the review surface's read paths (Phase-3 plan Build §1) -----------
    # RPC, NEVER a registry tool. A user-driven browse is not the model acting: routing
    # it through the registry would hand the model a ``list_directory`` capability as a
    # side effect AND raise a permission card in front of a click the person just made.
    # Both wrong. The precedent is snapshot restore, which is an RPC path and never a
    # tool. ``tests/test_review_surface_read_paths.py`` pins the registry against a
    # frozen tool-id set so the first half of that can never happen by accident.
    #
    # CONFINEMENT IS FOUR STEPS, IN THIS ORDER AND NO OTHER, in both handlers:
    #   1. the mode gate;
    #   2. resolve ONCE;
    #   3. ``_is_trusted_path`` — match-a-root THEN the floor, the shared resolver,
    #      reused exactly as the file tools' confinement uses it;
    #   4. pass ONLY the resolved value to the bridge.
    # Step 4 is why neither handler ever re-reads ``params`` after step 2: resolving
    # twice could approve one path and read another, which is the TOCTOU gap step 5
    # closed for the file tools and is the same gap on a read.

    def _browse_resolve(self, raw) -> str | None:
        """Step 2 for both handlers: ``expanduser`` + ``realpath``, ONCE. ``None`` when
        there is no usable path at all — a missing, blank or non-string argument, one
        that is not ABSOLUTE, or one the OS refuses to resolve.

        Absolute-only, exactly as ``grantTrust`` is: ``realpath`` would otherwise
        silently complete a relative path against the CORE PROCESS's working directory,
        which is a folder nobody chose and which no surface displays. A ``~someone``
        the OS cannot look up is the ordinary way to arrive here by accident —
        ``os.path.expanduser`` hands that back unchanged rather than raising — and
        answering "give me the full path" is the true thing to say about it.

        Never raises, for ``call_affected_path``'s reason and with its exception tuple:
        ``realpath`` raises ``ValueError`` on an embedded NUL and ``OSError`` on some
        malformed paths. ``RuntimeError`` is named too, though ``os.path.expanduser``
        does not raise it — ``Path.expanduser`` does, for that same ``~someone``, and
        that was a live turn-ending crash on the tool path until 2026-08-08. Naming it
        here costs nothing and means switching this line to ``Path`` cannot reintroduce
        it. A browse is a click, and a click must not be able to end a turn."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            expanded = os.path.expanduser(raw.strip())
            if not os.path.isabs(expanded):
                return None
            return os.path.realpath(expanded)
        except (OSError, ValueError, RuntimeError):
            return None

    def _trusted_root_for(self, resolved_path: str) -> str | None:
        """Which trusted root a RESOLVED path sits under — the longest match, so a root
        nested inside another names the nearer one. DISPLAY ONLY: the surface renders a
        path relative to it. Nothing is permitted by it, and ``None`` (a path whose root
        was revoked between two calls) is a rendering answer, never an authorization."""
        best: str | None = None
        for root in self._trusted_roots():
            if path_is_within(resolved_path, root) and (best is None or len(root) > len(best)):
                best = root
        return best

    def _browse_entries(self, directory: str, rows) -> list[dict]:
        """The shell's rows, plus ``escapes`` — computed HERE and never in Rust.

        ``escapes`` is one predicate applied to one more path: does this entry, resolved,
        still land inside trust? Duplicating that in the shell would give the app two
        answers to "is this inside the folder you trusted", and the day they disagreed
        the wrong one would be on screen. It is a UI honesty affordance — dim the row,
        say it points outside — and NEVER the boundary: the boundary is that the
        follow-up call refuses at step 3.

        The trust rows are read ONCE for the whole listing (up to 500 entries), through
        the same pure predicate ``_is_trusted_path`` calls. A store round-trip per row
        would put 500 queries on the worker thread behind every click, and every one of
        them would be asking the identical question."""
        roots = self._trusted_roots()
        data_dir = self._data_dir()
        entries: list[dict] = []
        for row in rows or []:
            name = row.get("name") if isinstance(row, dict) else None
            if not isinstance(name, str) or not name:
                continue
            child = self._browse_resolve(os.path.join(directory, name))
            entries.append(
                {
                    "name": name,
                    "kind": str(row.get("kind") or "other"),
                    "size": int(row.get("size") or 0),
                    # A DANGLING link is judged like any other: ``realpath`` follows it
                    # whether or not the target exists, so a link to a file that is not
                    # there yet, outside the folder, is still marked as leaving — which
                    # is the honest answer, since following it is what the refusal is
                    # about. ``None`` here is the defensive tail (a name the OS refuses
                    # outright, which a real listing cannot produce): unvouchable is
                    # treated as leaving, the direction that dims a row rather than the
                    # one that invites a click.
                    "escapes": child is None or not is_trusted(child, roots, data_dir),
                }
            )
        return entries

    def _workspace_list_directory(self, params: dict) -> dict:
        """workspace.listDirectory {directory} -> {directory, root, entries, truncated}
        | {ok:false, error}.

        ONE LEVEL, expansion-driven — there is deliberately no ``depth`` parameter, because
        a depth knob is how a full repo walk gets requested by accident.

        The mode gate is FIRST and it is load-bearing, not decorative: trust rows persist
        and nothing revokes them on a profile switch, so without it a Simple-profile
        window could browse a folder that was trusted under Developer. (Precedent:
        ``rpc/widgets.py``'s SAFE refusal.) It is also why this reads ``_mode()`` fresh
        rather than anything cached — a ``profile.set`` takes effect on the very next
        click, with no restart.

        Nothing is hidden: ``.git`` and ``node_modules`` are listed like everything else.
        Hiding is a lie about what is on disk, and telling the truth about what is on
        disk is this surface's only value; rendering them collapsed is the UI's job."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _BROWSE_NEEDS_DEVELOPER}
        resolved = self._browse_resolve(params.get("directory"))
        if resolved is None:
            return {"ok": False, "error": _BROWSE_NEEDS_A_FOLDER}
        if not self._is_trusted_path(resolved):
            return {"ok": False, "error": _BROWSE_NOT_TRUSTED}
        bridge = self._shell_bridge
        if bridge is None:
            return {"ok": False, "error": _BROWSE_NO_SHELL}
        try:
            # ONLY the resolved value crosses. The shell caps the listing at 500 entries
            # and keeps its own data-dir floor underneath this one.
            answer = bridge.list_workspace_directory(resolved)
        except RuntimeError as exc:
            # The shell's refusals are already plain sentences (CLAUDE.md); anything
            # without one becomes the generic line rather than an empty error.
            return {"ok": False, "error": str(exc) or _BROWSE_NO_SHELL}
        return {
            "directory": resolved,
            "root": self._trusted_root_for(resolved),
            "entries": self._browse_entries(resolved, answer.get("entries")),
            "truncated": bool(answer.get("truncated")),
        }

    def _workspace_read_file(self, params: dict) -> dict:
        """workspace.readFile {path} -> {path, root, content, bytes, truncated}
        | {ok:false, error}.

        Text to SHOW. This is not ``read_project_file`` with a different name and must
        never become it: that tool feeds a MODEL and therefore REFUSES an oversize file,
        while this feeds a person's eyes and TRUNCATES one and says so. The two live on
        separate shell methods for exactly that reason (``VIEW_SIZE_BOUND`` in
        filesystem.rs owns the numbers), and the same four confinement steps govern both
        handlers here."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _BROWSE_NEEDS_DEVELOPER}
        resolved = self._browse_resolve(params.get("path"))
        if resolved is None:
            return {"ok": False, "error": _BROWSE_NEEDS_A_FILE}
        if not self._is_trusted_path(resolved):
            return {"ok": False, "error": _BROWSE_NOT_TRUSTED}
        bridge = self._shell_bridge
        if bridge is None:
            return {"ok": False, "error": _BROWSE_NO_SHELL}
        try:
            answer = bridge.read_workspace_file_for_view(resolved)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc) or _BROWSE_NO_SHELL}
        content = answer.get("content")
        if not isinstance(content, str):
            return {"ok": False, "error": _BROWSE_NO_SHELL}
        return {
            "path": resolved,
            "root": self._trusted_root_for(resolved),
            "content": content,
            # The FILE's size, not the excerpt's — they differ exactly when `truncated`
            # is true, which is when the difference is the number worth showing. Falls
            # back to what actually arrived, never to zero.
            "bytes": int(answer.get("bytes") or len(content.encode("utf-8"))),
            "truncated": bool(answer.get("truncated")),
        }

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
