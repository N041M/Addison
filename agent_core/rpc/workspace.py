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
from agent_core.snapshots.file_revert import (
    FileEdit,
    another_file_stands_there,
    replaced_by_a_link,
)
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
# The diff's own refusal (Build §2). Its own sentence rather than the not-trusted one,
# because it answers a different question: this path may be perfectly inside a folder
# you trusted and simply be a file Addison has not changed — or one whose change has
# already been put back.
_NO_EDIT_TO_SHOW = "Addison hasn't made a change to that file that's still in place."
# The diff's half of the planted-shortcut refusal (``file_revert.replaced_by_a_link``
# owns the predicate and the reasoning, and words the revert's half beside the write it
# refuses). This one names no mechanism either: what is true is that the file is not
# there any more and something pointing elsewhere is.
_LINK_STANDS_THERE = (
    "That file has been replaced by a shortcut to somewhere else, so Addison won't "
    "open it."
)
# The same refusal for the swap a symlink check cannot see — a hard link, or any other
# way the name came to reach a different file since Addison wrote it
# (``file_revert.another_file_stands_there``). Worded for the VIEWER, like the sentence
# above it: what a person needs told is that this is not the file they think they are
# looking at.
_ANOTHER_FILE_STANDS_THERE = (
    "A different file is at that name now, so this isn't the change Addison made."
)


def _relative_to(path: str, root: str | None) -> str:
    """What the surface shows by default. With no root — the row's trust was revoked
    between the write and now — the WHOLE path, never a basename: a bare name for a file
    nobody can place is less useful than the long answer, and the long answer is true."""
    if not root:
        return path
    try:
        return os.path.relpath(path, root)
    except ValueError:
        # Different drives on Windows. The absolute path is always sayable.
        return path


def _root_for(resolved_path: str, roots: list[str]) -> str | None:
    """Which of ``roots`` a RESOLVED path sits under — the longest match, so a root
    nested inside another names the nearer one. DISPLAY ONLY: the surface renders a path
    relative to it. Nothing is permitted by it, and ``None`` (a path whose root was
    revoked between two calls) is a rendering answer, never an authorization.

    ROOTS IN, so a caller with a list to render reads the trust rows ONCE and answers
    every row from that one read — ``_browse_entries``'s rule, which it states for 500
    entries and which the edits list broke for 200: one store round trip per row, on the
    worker thread, behind a single click, every one of them asking the identical
    question. Pure, so both callers can share it without either owning a store."""
    best: str | None = None
    for root in roots:
        if path_is_within(resolved_path, root) and (best is None or len(root) > len(best)):
            best = root
    return best


def _diff_payload(edit: FileEdit, *, after: str, truncated: bool) -> dict:
    """The two panes, worded once — a deleted file takes the same shape as any other
    diff (BEFORE against nothing), so no caller can tell the two apart by shape and then
    render them differently."""
    return {
        "path": edit.path,
        "before": edit.before,
        "after": after,
        "beforeTruncated": False,
        "afterTruncated": truncated,
    }


def _disk_state(wrote_sha256: str | None, digest) -> dict:
    """``onDiskChanged`` + ``missing``, from what Addison recorded and what the shell
    found. THREE ANSWERS, not two:

      * ``True``  — what is there now is not what Addison wrote. The confirm must warn
        before a revert replaces it.
      * ``False`` — the file is byte-for-byte as Addison left it.
      * ``None``  — Addison CAN'T TELL, and says so. A row written before
        ``wrote_sha256`` existed, a file the shell could not judge, or no shell at all.
        Guessing ``False`` here would be the dangerous guess: it is the one that lets a
        revert throw away somebody's own work without a word.

    A file that is GONE reports ``missing`` and ``onDiskChanged: None``. Its absence is
    a change, but it is not the change that sentence describes, and the surface has a
    different thing to say about it."""
    found = digest if isinstance(digest, dict) else {}
    missing = bool(found.get("missing"))
    on_disk = found.get("sha256")
    if missing or not wrote_sha256 or not isinstance(on_disk, str):
        return {"onDiskChanged": None, "missing": missing}
    return {"onDiskChanged": on_disk != wrote_sha256, "missing": False}


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
        it. A browse is a click, and a click must not be able to end a turn.

        ``strip()`` DECIDES ONLY WHETHER THERE IS ANYTHING HERE — the path itself is
        passed through untouched. Stripping it resolved ``report.txt `` (a legal
        filename, on every filesystem Addison runs on) to ``report.txt``, and the viewer
        then showed one file's bytes under the other one's name. A leading space is the
        mirror of that and lands where it should: `` /p/a.txt`` is not an absolute path,
        so it is refused rather than quietly turned into one."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            expanded = os.path.expanduser(raw)
            if not os.path.isabs(expanded):
                return None
            return os.path.realpath(expanded)
        except (OSError, ValueError, RuntimeError):
            return None

    def _edit_resolve(self, raw) -> str | None:
        """Step 2 for the two MEMBERSHIP handlers: ``_browse_resolve``'s contract —
        expanduser, absolute-only, never raises — resolving the FOLDERS a path names
        and leaving its last component alone.

        A SIBLING rather than a flag on ``_browse_resolve``, because the two answer
        different questions. A browse asks "which file on disk is this", and following
        the last component is the whole of it. These two ask "which RECORDED EDIT is
        this", and a recorded edit is a NAME — one whose last component was not a
        shortcut when Addison wrote it (``affected_path`` resolves before the write).

        They have to ask it the same way ``revert_key`` asks it of the rows, or the two
        stop agreeing exactly when it matters: with a shortcut standing at a written
        path, a fully-resolved parameter keys off the shortcut's TARGET while the row
        keys off the name — so the click would find no edit at all and the person would
        be told Addison has no change in that file, which is false, and the guard that
        should have answered them would never be reached. Resolving the folders is what
        makes an alias of the project (or ``~``) still land on the row.

        The cost, stated: a parameter that reaches a written file THROUGH a shortcut no
        longer finds its row. Nothing produces one — the surface hands back the ``path``
        it was given — and the answer is a plain refusal rather than anything worse."""
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            expanded = os.path.expanduser(raw)
            if not os.path.isabs(expanded):
                return None
            parent, name = os.path.split(expanded)
            if not name:
                # A path naming a folder ("/a/b/"). There is no last component to keep,
                # and it matches no edit either way.
                return os.path.realpath(expanded)
            return os.path.join(os.path.realpath(parent), name)
        except (OSError, ValueError, RuntimeError):
            return None

    def _trusted_root_for(self, resolved_path: str) -> str | None:
        """``_root_for`` against a fresh read of the trust rows — for the callers that
        answer about ONE path. A caller rendering a LIST reads the rows once itself and
        calls ``_root_for`` directly (see ``_workspace_list_edits``)."""
        return _root_for(resolved_path, self._trusted_roots())

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

    # --- the review surface's diff + revert (Phase-3 plan Build §2/§3) ------
    # THE DATA ALREADY EXISTS. Every ``write_project_file`` snapshot records the bytes
    # that were there BEFORE it, and the shell refuses to overwrite a binary or oversize
    # file, so every BEFORE in the table is text and ≤256 KiB. AFTER is the file on disk.
    # No new capture table, no snapshot-scope change.
    #
    # CONFINEMENT IS THE SAME FOUR STEPS, with step 3 substituted and the substitution
    # stated rather than assumed:
    #   1. the mode gate — unchanged, and load-bearing for ``listDirectory``'s reason;
    #   2. resolve ONCE, through ``_edit_resolve`` — ``_browse_resolve``'s contract with
    #      the last component left alone, so the question this asks of a parameter is
    #      the question ``revert_key`` asks of a row (that sibling's docstring owns why);
    #   3. MEMBERSHIP, not live trust: the resolved path must be one Addison itself
    #      wrote and has not yet put back. That is a CLOSED SET this process produced,
    #      which is a narrower thing than "somewhere under a folder you trusted" — the
    #      raw parameter never reaches disk, only a key that matched a row does.
    #      Live trust is deliberately NOT the test here, because the plan requires an
    #      edit whose trust was revoked to stay listed (with ``root: null``) and to stay
    #      revertable: the shell's undo path has never asked about trust either (its
    #      ledger is session, not trust), so asking here would produce a surface that
    #      shows a change, offers to put it back, and refuses to show you what it is.
    #   4. pass ONLY THE RECORDED PATH — ``undo_payload["path"]``, carried by
    #      ``FileEdit.path``; never ``params`` and never a fresh resolution of the
    #      stored value. Membership is the whole confinement here, so what crosses has
    #      to be the value that was confined and not one derived from it afterwards:
    #      re-resolving at read time meant a shortcut appearing at a written path moved
    #      every later call off the approved value, which put a file outside every
    #      trusted folder into the diff's AFTER pane. The GROUPING key may be derived
    #      (``file_revert.revert_key`` asks the filesystem, and must); what is handed to
    #      the shell may not. Then, because the shell follows what it is given, the
    #      diff asks ``replaced_by_a_link`` before it reads at all.
    # Two batch questions, two SIBLINGS rather than one shared helper taking the method
    # and the key — the reason ``filesystem.rs`` gives for keeping its two oversize
    # refusals apart: a function whose entire body is the arguments its callers pass in
    # is indirection, not reuse. Both answer ``{}`` rather than raising, because
    # ``revertable`` / ``onDiskChanged`` / ``missing`` are honesty affordances and a
    # listing that died because one of them could not be computed would be the least
    # honest outcome available.
    def _restorable_map(self, paths: list[str]) -> dict:
        bridge = self._shell_bridge
        if bridge is None or not paths:
            return {}
        try:
            answer = bridge.can_restore_workspace_files(paths)
        except RuntimeError:
            return {}
        restorable = answer.get("restorable")
        return restorable if isinstance(restorable, dict) else {}

    def _digest_map(self, paths: list[str]) -> dict:
        bridge = self._shell_bridge
        if bridge is None or not paths:
            return {}
        try:
            answer = bridge.digest_workspace_files(paths)
        except RuntimeError:
            return {}
        digests = answer.get("digests")
        return digests if isinstance(digests, dict) else {}

    def _workspace_list_edits(self) -> dict:
        """workspace.listEdits {} -> {edits, truncated} | {ok:false, error}.

        METADATA ONLY. The before/after text of twenty edits is megabytes on one JSON
        line across two process boundaries, for a list whose job is to let somebody pick
        ONE of them — so the text arrives per file, from ``readEditDiff``.

        Three fields need the shell and none of them may fail the list: ``revertable``
        (its session write ledger) and ``onDiskChanged`` / ``missing`` (what is on disk
        now). Both are single batch calls, keyed by path, for the reason ``escapes``
        reads the trust rows once — a per-file round trip would put two hundred of them
        behind one click."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _BROWSE_NEEDS_DEVELOPER}
        pending = self.file_revert_manager.pending_edits()
        paths = [edit.path for edit in pending.edits]
        restorable = self._restorable_map(paths)
        digests = self._digest_map(paths)
        # THE TRUST ROWS ONCE, for the whole list — the third of the three per-row
        # round trips this method used to make and the only one that was not already
        # batched. ``_browse_entries`` reads them once for 500 entries and its comment
        # forbids exactly what this did for 200.
        roots = self._trusted_roots()
        return {
            "edits": [
                self._edit_payload(
                    edit, restorable.get(edit.path), digests.get(edit.path), roots
                )
                for edit in pending.edits
            ],
            "truncated": pending.truncated,
        }

    def _edit_payload(self, edit: FileEdit, restorable, digest, roots: list[str]) -> dict:
        """One ``Edit`` on the wire. ``relativePath`` is what the UI renders by default;
        with no root to be relative to (trust revoked between the write and now) it is
        the whole path, because a bare basename would name a file the person cannot
        place.

        ``roots`` is passed IN rather than read here: this runs once per edit, and the
        caller has already read them once for the whole list."""
        root = _root_for(edit.path, roots)
        return {
            "path": edit.path,
            "root": root,
            "relativePath": _relative_to(edit.path, root),
            # NEWEST FIRST — the revert chain, in the order the table answered.
            "snapshotIds": list(edit.snapshot_ids),
            "writes": edit.writes,
            "created": edit.created,
            "firstWrittenAt": edit.first_written_at,
            "lastWrittenAt": edit.last_written_at,
            # The SHELL's answer about its own session ledger, never a permission and
            # never an inference: false here means "Addison cannot put this back", which
            # after a restart is true of every historic edit.
            "revertable": bool(restorable),
            **_disk_state(edit.wrote_sha256, digest),
        }

    def _workspace_read_edit_diff(self, params: dict) -> dict:
        """workspace.readEditDiff {path} -> {path, before, after, beforeTruncated,
        afterTruncated} | {ok:false, error}.

        BEFORE from the database, AFTER from disk. The two panes therefore answer
        different questions on purpose: the left is what Addison found, the right is
        what is there NOW — including anything the person has done to it since, which is
        exactly what ``onDiskChanged`` warns about before a revert throws it away.

        A file that is GONE is a DIFF, not a refusal: BEFORE against nothing, which is
        exactly what happened to it. The alternative — refusing because the read failed —
        would withhold the one thing the person can still act on (the earlier text, to
        copy) precisely when it is the only thing left. The read error is only trusted to
        mean "gone" when the shell says so through the digest; anything else is a plain
        sentence, because a diff whose right pane is empty because the shell was
        unreachable would be a lie in the shape of a deletion.

        ``beforeTruncated`` is always false in this tree and is on the wire anyway. The
        shell refuses to overwrite a file whose prior exceeds its capture bound, so a
        stored BEFORE is whole by construction — the field exists so that a later change
        to that bound cannot make the left pane quietly incomplete with nowhere to say
        so."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _BROWSE_NEEDS_DEVELOPER}
        resolved = self._edit_resolve(params.get("path"))
        if resolved is None:
            return {"ok": False, "error": _BROWSE_NEEDS_A_FILE}
        edit = self.file_revert_manager.chain_for(resolved)
        if edit is None:
            return {"ok": False, "error": _NO_EDIT_TO_SHOW}
        bridge = self._shell_bridge
        if bridge is None:
            return {"ok": False, "error": _BROWSE_NO_SHELL}
        # AND THEN THE DESTINATION, not only the value. ``edit.path`` is the path
        # confinement approved, recorded at write time — but a confined VALUE is not a
        # confined DESTINATION: ``shell.readWorkspaceFileForView`` opens the path, and
        # the OS follows a shortcut planted there since. That is how a diff becomes a
        # read of a private key, and it is why this asks even though step 3 already
        # matched a row. ``replaced_by_a_link`` owns why it has no false positive.
        if replaced_by_a_link(edit.path):
            return {"ok": False, "error": _LINK_STANDS_THERE}
        # AND THE SWAP A SYMLINK CHECK CANNOT SEE. A hard link planted at a written path
        # makes the name reach a file this edit never wrote, and the pane would render
        # that file's text as "what is there now" under this edit's BEFORE — a diff of two
        # unrelated files. Same question the revert asks before it writes, asked here
        # before anything is read, for ``replaced_by_a_link``'s reason.
        if another_file_stands_there(edit.path, edit.identities):
            return {"ok": False, "error": _ANOTHER_FILE_STANDS_THERE}
        try:
            # The RECORDED path, never the parameter and never a re-resolution of
            # either — and the viewer's read, not the tool's: this feeds a person's
            # eyes, so an oversize file is truncated on a character boundary and said
            # so, never refused.
            answer = bridge.read_workspace_file_for_view(edit.path)
        except RuntimeError as exc:
            if self._digest_map([edit.path]).get(edit.path, {}).get("missing"):
                return _diff_payload(edit, after="", truncated=False)
            return {"ok": False, "error": str(exc) or _BROWSE_NO_SHELL}
        after = answer.get("content")
        if not isinstance(after, str):
            return {"ok": False, "error": _BROWSE_NO_SHELL}
        return _diff_payload(edit, after=after, truncated=bool(answer.get("truncated")))

    def _workspace_revert_file(self, params: dict) -> dict:
        """workspace.revertFile {path} -> {ok, path, detail} | {ok:false, error}.

        The WHOLE unreverted chain for that path, in one write, to a state that actually
        existed on disk — ``snapshots/file_revert.py`` owns the semantics and the reason
        it is a third mechanism rather than a use of ``UndoManager``.

        Running on the worker thread is what serialises this behind an in-flight turn:
        no extra locking, because the queue already is one."""
        self._ensure_built()
        if self._mode() is not PolicyMode.OPEN:
            return {"ok": False, "error": _BROWSE_NEEDS_DEVELOPER}
        resolved = self._edit_resolve(params.get("path"))
        if resolved is None:
            return {"ok": False, "error": _BROWSE_NEEDS_A_FILE}
        result = self.file_revert_manager.revert_path(resolved)
        if not result.ok:
            return {"ok": False, "error": result.detail}
        return {"ok": True, "path": result.path, "detail": result.detail}

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
