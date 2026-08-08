"""Per-file revert — the review surface's sharp edge (phase-3 plan Build §3).

THE THIRD MECHANISM, and it is deliberately not either of the other two.
``UndoManager`` (§4.5) is LIFO, tool-agnostic, redo-stack-owning and coupled to the
orchestrator through ``record()``; ``SnapshotManager`` (G3) restores CONFIG, whole,
from a captured row. This is per-path, out-of-order, chain-collapsing,
redo-stack-untouching and ``write_project_file``-only. CLAUDE.md already draws that
line for the first two — "complementary, independent, and they never call each
other" — and this module holds to it: it imports neither, and nothing in it may.

**Chain semantics: reverting a path reverts the ENTIRE unreverted chain for it, down
to its OLDEST unreverted prior.** That is a state which actually existed on disk —
the direct analogue of the snapshot floor's "a restore lands somewhere that actually
ran" — and it leaves ZERO unreverted rows for the path, which is what makes the
out-of-order hazard evaporate by construction rather than by care:

    F is v0. Addison writes v1 (S1, prior v0), then v2 (S2, prior v1). Revert S1
    ALONE and disk is v0 while S2 is still unreverted with prior v1 — so the
    person's next "Undo last action" writes v1 and RESURRECTS the content they
    just reverted away from. Nothing would notice.

Reverting S1 and S2 together to v0 cannot produce that, and the diff's BEFORE pane is
that same oldest prior, so what Revert produces is exactly what the person is looking
at.

**No hunk-level or partial revert, ever.** Not an omission — a decision. Putting back
some of a file's lines would require writing a byte combination that never existed on
disk, which is precisely what "lands somewhere that actually ran" forbids. A person
who wants half of it has the BEFORE pane in front of them and can copy from it.

**Never touch ``UndoManager._redo_stack``** — not push, not clear. ``WriteProjectFileTool``
has no ``redo()``, so a pushed entry would make ``can_redo()`` true and surface a "Do
that again" control that always fails; and clearing would discard a legitimately
redoable ``save_file`` undo belonging to a different mechanism. This module holds no
reference to an ``UndoManager``, so neither is reachable from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agent_core.tools.base import ActionSnapshot

#: The ONE tool this mechanism knows about. A per-file revert is meaningful only for a
#: snapshot whose payload names a file and carries the bytes that were there before it;
#: widening this to "any undoable tool" would be a second undo manager with a worse
#: interface, not a feature.
WRITE_TOOL_ID = "write_project_file"

#: How many unreverted write rows one read of the table may carry — the bound
#: prerequisite 3 deferred to this build (plan Build §2). Retention deletes REVERTED
#: rows only, so this subset is bounded by nothing on disk and every read of it has to
#: bound itself.
#:
#: ROWS, not files: the plan's number, and the conservative reading. A window of rows
#: can cut a long chain, and it is worth stating why that is safe in the one direction
#: that matters. Rows arrive newest-first, so any row of a path that falls OUTSIDE the
#: window is strictly OLDER than every row of it inside. A revert therefore lands the
#: file on a real earlier state and leaves behind only rows older still — and the undo
#: button acting on one of those moves the file FURTHER BACK, never forward. Forward is
#: the resurrection this design exists to prevent.
#:
#: ONE window for all three operations (list, diff, revert) so they cannot disagree
#: about where a chain begins: a diff whose BEFORE is not what Revert produces would be
#: worse than no diff.
_MAX_EDITS = 200

# Plain-language answers (D6). Each says what is true and what to do next, and none
# names a mechanism.
_NOTHING_TO_PUT_BACK = "Addison has no change of its own to put back for that file."
_NO_SHELL = "Addison can't put that file back just now. Please try again."
_COULD_NOT_PUT_BACK = "Addison couldn't put that file back. Nothing was changed."
_MARKED_NOTHING = (
    "Addison put the file back, but couldn't update its own record of the change. "
    "If it still shows as changed, try again."
)
#: Something else stands at the name Addison wrote. The diff's half of this refusal is
#: worded in ``rpc/workspace.py`` beside the handler that produces it — two sentences
#: rather than one shared, because they differ in the thing a person needs told: the
#: viewer's says this is not the file they think they are looking at, and this one has
#: to say that nothing was written, the same reassurance ``_COULD_NOT_PUT_BACK``
#: carries and for the same reason.
_LINK_STANDS_THERE_WRITE = (
    "That file has been replaced by a shortcut to somewhere else, so Addison won't "
    "put it back. Nothing was changed."
)


@dataclass(frozen=True)
class FileEdit:
    """Every unreverted write Addison has made to ONE file, collapsed into one thing a
    person can look at and act on. Frozen: this is a reading of the table at a moment,
    never a handle to mutate it through."""

    #: The file, AS RECORDED AT WRITE TIME — ``undo_payload["path"]`` of the oldest
    #: unreverted row, which is the value confinement approved and the one whose
    #: ``prior`` a revert restores. This is what every later call carries: the diff's
    #: read, the ledger question, the digest, the path the shell is asked to write —
    #: exactly as ``WriteProjectFileTool.undo()`` uses that same key of that same row.
    #:
    #: NEVER a fresh resolution of it. Re-resolving at read time moved this value off
    #: the approved one the moment anything changed underneath the name — a symlink
    #: appearing at a written path was enough to send the diff's read, and the webview's
    #: AFTER pane, to a file nobody trusted. The grouping key (``revert_key``) is
    #: derived and may move; what crosses to the shell is this.
    path: str
    #: The chain, NEWEST FIRST — the same order the table answers in, so "the newest
    #: write" is ``[0]`` and "the oldest still-unreverted write" is ``[-1]`` everywhere.
    snapshot_ids: tuple[str, ...]
    #: How many unreverted writes collapsed into this one edit.
    writes: int
    #: Addison CREATED this file: the oldest unreverted write found nothing there, so
    #: the BEFORE pane is empty and putting it back means removing the file.
    created: bool
    first_written_at: int
    last_written_at: int
    #: The BEFORE state: the ``prior`` of the OLDEST unreverted write, which is where a
    #: revert lands. Empty for a file Addison created — there was nothing there.
    #: Always text and always ≤256 KiB, because the shell refuses to overwrite a binary
    #: or oversize file at capture time (``capture_prior_text``), so no read here can
    #: be truncated or undecodable.
    before: str = field(repr=False)
    #: What Addison last wrote, hashed at write time — or ``None`` for a row written
    #: before that key existed, which is why "has the file changed since" is a
    #: three-valued question rather than a two-valued one.
    wrote_sha256: str | None


@dataclass(frozen=True)
class PendingEdits:
    edits: tuple[FileEdit, ...]
    #: The table held more unreverted writes than the window carries. Say so: a list
    #: that is quietly incomplete is indistinguishable from an edit that is not there.
    truncated: bool


@dataclass(frozen=True)
class FileRevertResult:
    ok: bool
    path: str
    #: One plain sentence, ready to show. Never an exception's text unless the shell
    #: itself wrote it (the shell's refusals are already plain language).
    detail: str
    #: The rows this settled — empty on every failure, including the one where the
    #: file WAS put back and only the record failed.
    snapshot_ids: tuple[str, ...] = ()
    #: The revert removed the file, because Addison had created it.
    deleted: bool = False


def revert_key(raw: object) -> str | None:
    """The identity two writes to the same file share — **asked of the filesystem,
    never inferred from the spelling**. ``file:{st_dev}:{st_ino}`` when the OS can
    still find something at that name, ``path:{normpath(realpath(...))}`` when it
    cannot. GROUPING ONLY: this value never crosses to the shell (see ``FileEdit.path``).

    That is ``os.path.samefile`` semantics, one path at a time so the answer can be a
    dict key — and it is the only answer that is right on both kinds of volume, because
    each of the two guesses is wrong on one of them:

      * **Compare the resolved spelling** (what this did until 2026-08-08, over a
        comment claiming two spellings are "two genuinely different files on any
        case-sensitive filesystem"). Addison ships on macOS, whose default volume is
        case-INSENSITIVE, so ``Notes.md`` and ``notes.md`` are ONE file with TWO chains:
        write v0->v1 under one spelling and v1->v2 under the other, and reverting them
        in the wrong order lands the file on v1 with v0 unreachable — the exact
        resurrection the chain collapse at the top of this module exists to make
        impossible by construction.
      * **Casefold** (``policy._canonical``, unconditionally). On a case-sensitive
        volume — APFS case-sensitive, an external or network mount — the two names ARE
        two files, and merging them would write one file's prior bytes into the other.
        KNOWN-GAPS flags that casefold; this is the call site where it would do harm.

    Asking the OS is right in both: one inode reached twice collapses, two inodes stay
    apart, and hard links to one file collapse for the same reason they should — writing
    either name changes the same bytes.

    ``lstat``, never ``stat``: the question is which directory entry this NAME reaches,
    and following a symlink that appeared after the write would move the identity onto a
    file confinement never saw. (What that same appearance must NOT move is the path
    handed to the shell — that is ``FileEdit.path``, which is recorded, not resolved.)

    WHEN THERE IS NOTHING TO ASK ABOUT — a file Addison created and the person deleted,
    the ordinary case, and the reason this cannot simply be "stat or nothing" — the
    stored path is the tiebreak and it is compared EXACTLY. Not casefolded: a wrong
    merge writes bytes into a file nobody named, while a wrong split only leaves two
    rows where one would do, and both are then reverted to real earlier states. The
    residual (two spellings of a file that is no longer there stay two chains on a
    case-insensitive volume) is recorded in docs/KNOWN-GAPS.md.

    Never raises, with ``call_affected_path``'s exception tuple and for its reason: a
    single unreadable row must not take a listing down with it. ``None`` means "no
    usable path here", and such a row is skipped rather than shown."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        entry = os.lstat(raw)
    except (OSError, ValueError):
        entry = None
    if entry is not None:
        # BOTH numbers. An inode number is unique only within its device, so a path on
        # a mounted volume could otherwise collide with an unrelated file on the boot
        # disk — and this key decides which bytes a revert writes.
        return f"file:{entry.st_dev}:{entry.st_ino}"
    try:
        # A prefix apiece, so a fallback key can never collide with a stat'd one.
        return f"path:{os.path.normpath(os.path.realpath(raw))}"
    except (OSError, ValueError, RuntimeError):
        return None


def replaced_by_a_link(path: str) -> bool:
    """Is a SHORTCUT standing where Addison wrote a file? Then no read and no write of
    ours may follow it.

    DEFENCE IN DEPTH, and it is not redundant with handing the shell the recorded path.
    The recorded value is confined; the DESTINATION of that value is not, because
    ``shell.readWorkspaceFileForView`` opens the path and ``restore_workspace_path``
    writes it, and the OS follows a symlink in both. So a link planted at a written path
    turns a diff into a read of somebody's private file and a revert into a write
    through to it — with the ledger check passing, because the ledger holds the NAME.

    NO LEGITIMATE FALSE POSITIVE. ``write_project_file.affected_path`` resolves before
    the write (``Path(raw).expanduser().resolve()`` — the final component included, and
    a dangling link resolves to its target's name), so a recorded path is never a
    symlink at write time. One standing there now appeared afterwards, and following it
    is exactly what must not happen.

    A path that is simply GONE is deliberately NOT this. ``os.path.islink`` answers
    False for anything it cannot stat and never raises, which is the answer this wants:
    a file Addison created and the person deleted must still revert (there is nothing
    there to follow), and a deletion is a state the diff and the revert both already
    describe honestly."""
    return os.path.islink(path)


class FileRevertManager:
    """Reads unreverted ``write_project_file`` snapshots; puts one file back.

    Holds a store and a shell bridge and nothing else — no registry (there is exactly
    one tool this concerns and it is named above), no undo manager (see the module
    docstring), no policy. Confinement and the mode gate are the RPC layer's, exactly
    as they are for every other path that reaches the filesystem."""

    def __init__(self, store, shell_bridge=None) -> None:
        self._store = store
        # Used ONLY by revert_path, and only to write. The bridge is how every
        # filesystem EFFECT leaves this process (§1.3), and no effect and no file's
        # contents are reachable from here without it. The one thing this module asks
        # the OS directly is whether two names are one file (``revert_key``), which is
        # metadata, changes nothing, and is the same class of question every
        # ``realpath`` in the RPC layer already asks.
        self._shell_bridge = shell_bridge

    # --- reading -----------------------------------------------------------
    def pending_edits(self, limit: int = _MAX_EDITS) -> PendingEdits:
        """Every file Addison has changed that is STILL changed, newest first.

        Metadata and the BEFORE text only — no AFTER, because this class never reads a
        file's contents. What is on disk now is the shell's answer, and the RPC layer
        asks for it.
        """
        groups, truncated = self._grouped(limit)
        return PendingEdits(
            edits=tuple(self._edit(chain) for chain in groups.values()),
            truncated=truncated,
        )

    def chain_for(self, path: str) -> FileEdit | None:
        """The one edit for ``path``, or ``None`` when Addison has no unreverted write
        to it. The diff's read — and it goes through exactly the same window and the
        same grouping as ``pending_edits``, so what the diff shows and what the list
        promised cannot come apart."""
        key = revert_key(path)
        if key is None:
            return None
        groups, _truncated = self._grouped(_MAX_EDITS)
        chain = groups.get(key)
        return self._edit(chain) if chain else None

    # --- the write ---------------------------------------------------------
    def revert_path(self, path: str) -> FileRevertResult:
        """Put ONE file back to the oldest state Addison found it in.

        ONE WRITE, computed once. Replaying N undos would do N shell writes, briefly
        put intermediate states on disk, and could strand the file mid-chain if one of
        them failed — so the target is computed from the oldest unreverted row and
        written once.

        WRITE FIRST, MARK SECOND, and the asymmetry is the correct way round:

          * a failed WRITE marks nothing, so the edits stay listed and stay live —
            which is the truth, the file was not changed;
          * a failed MARK leaves rows claiming unreverted for a file that already sits
            at its prior, and re-reverting computes the same target and writes the same
            bytes. Idempotent, and it converges on the next attempt.

        The reverse order could mark a chain reverted for a file that never moved,
        which is the one failure this mechanism must not have: it would drop the rows
        out of every list and leave the person no way back at all.

        THE PATH WRITTEN IS THE PATH RECORDED, never the argument and never a fresh
        resolution of either: the row that supplies the bytes supplies the name they go
        back to, which is precisely the pairing ``write_project_file.undo()`` uses."""
        key = revert_key(path)
        if key is None:
            return FileRevertResult(ok=False, path=str(path), detail=_NOTHING_TO_PUT_BACK)
        groups, _truncated = self._grouped(_MAX_EDITS)
        chain = groups.get(key)
        if not chain:
            return FileRevertResult(ok=False, path=str(path), detail=_NOTHING_TO_PUT_BACK)
        if self._shell_bridge is None:
            return FileRevertResult(ok=False, path=str(path), detail=_NO_SHELL)

        oldest = chain[-1]
        # The CONFINED value — see ``FileEdit.path``. ``_grouped`` admits no row whose
        # payload path is not a string, so this is one.
        target = oldest.undo_payload["path"]
        if replaced_by_a_link(target):
            return FileRevertResult(ok=False, path=target, detail=_LINK_STANDS_THERE_WRITE)
        existed = bool(oldest.undo_payload.get("existed"))
        # ``None`` means "there was nothing there", which the shell turns into a
        # delete — the same contract ``write_project_file.undo()`` uses, because it is
        # the same question asked of the same row.
        prior = (oldest.undo_payload.get("prior") or "") if existed else None
        try:
            self._shell_bridge.restore_workspace_file(target, prior)
        except RuntimeError as exc:
            # The shell's own refusals are already plain sentences (CLAUDE.md), and
            # they say the useful thing — "Addison can only undo a file change it
            # made", for one.
            return FileRevertResult(ok=False, path=target, detail=str(exc) or _NO_SHELL)
        except Exception:
            # ANYTHING ELSE IS A BUG, and a bug's text is not a sentence for a person:
            # ``main._plain`` draws exactly this line for the same reason. The first
            # generated fixture of this payload caught the version that did not, and
            # what it put on the wire was "'_FixtureEditBridge' object has no attribute
            # 'restore_workspace_file'".
            return FileRevertResult(ok=False, path=target, detail=_COULD_NOT_PUT_BACK)

        ids = tuple(snapshot.id for snapshot in chain)
        try:
            # ONE statement, ONE commit (Store.mark_snapshots_reverted): a crash may
            # not leave half a chain marked while the file already sits at its prior.
            self._store.mark_snapshots_reverted(ids)
        except Exception:
            return FileRevertResult(ok=False, path=target, detail=_MARKED_NOTHING)
        return FileRevertResult(
            ok=True,
            path=target,
            detail=_reverted_sentence(target, deleted=not existed),
            snapshot_ids=ids,
            deleted=not existed,
        )

    # --- internals ---------------------------------------------------------
    def _grouped(self, limit: int) -> tuple[dict[str, list[ActionSnapshot]], bool]:
        """One read of the window, grouped by ``revert_key``, each chain newest-first.

        ``limit + 1`` rows are asked for so "there are more" is a fact rather than a
        guess: a window that comes back exactly full is indistinguishable from a table
        that ends there, and the difference is what ``truncated`` reports."""
        rows = self._store.unreverted_snapshots_for_tool(WRITE_TOOL_ID, limit + 1)
        truncated = len(rows) > limit
        groups: dict[str, list[ActionSnapshot]] = {}
        # ONE key per distinct spelling per read, and the memo is doing two jobs. It
        # keeps 200 rows of one much-written file to a single syscall — but the reason
        # it is not merely an optimisation is that ``revert_key`` asks the filesystem,
        # and a file deleted between two lookups of the SAME string would otherwise
        # answer differently for two rows of one chain and split it. A split chain
        # leaves a row behind, which is the resurrection this module exists to prevent.
        keys: dict[str, str | None] = {}
        for row in rows[:limit]:
            raw = row.undo_payload.get("path")
            if not isinstance(raw, str):
                continue
            if raw not in keys:
                keys[raw] = revert_key(raw)
            key = keys[raw]
            if key is None:
                continue
            groups.setdefault(key, []).append(row)
        return groups, truncated

    @staticmethod
    def _edit(chain: list[ActionSnapshot]) -> FileEdit:
        """N writes to one file collapse into ONE edit: the BEFORE is the oldest
        unreverted prior (where a revert lands) and the digest is the NEWEST write's
        (what should be on disk now if nobody else has touched it). Taking either from
        the other end would make the diff and the "changed since" warning describe two
        different moments.

        The PATH comes from the same oldest row, for the same reason its bytes do —
        see ``FileEdit.path``. Where a chain holds two spellings of one file (a
        case-insensitive volume), that is the spelling Addison recorded beside the
        bytes a revert restores."""
        newest, oldest = chain[0], chain[-1]
        existed = bool(oldest.undo_payload.get("existed"))
        digest = newest.undo_payload.get("wrote_sha256")
        return FileEdit(
            path=oldest.undo_payload["path"],
            snapshot_ids=tuple(snapshot.id for snapshot in chain),
            writes=len(chain),
            created=not existed,
            first_written_at=int(oldest.created_at),
            last_written_at=int(newest.created_at),
            before=(oldest.undo_payload.get("prior") or "") if existed else "",
            wrote_sha256=digest if isinstance(digest, str) and digest else None,
        )


def _reverted_sentence(path: str, *, deleted: bool) -> str:
    """What the person is told. The file's NAME, never its full path — the same rule
    every tool label and permission card follows."""
    name = os.path.basename(path) or path
    if deleted:
        return f"Removed {name} — Addison had created it."
    return f"Put {name} back the way it was before Addison changed it."
