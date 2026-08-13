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

**What makes two writes ONE chain is recorded WHEN THEY HAPPEN, never asked afterwards**
(2026-08-08). ``write_project_file`` stores the identity of the file each write landed
on (``IDENTITY_KEY``, minted by ``revert_key``) beside the path it wrote, and a chain is
the set of rows joined by either recorded fact: the same NAME, or the same FILE. Both
are facts about the past, so nothing done to the filesystem afterwards can merge two
chains that were never one — which asking the disk at READ time could, and did:

    Addison edits a.txt and b.txt. Somebody runs ``rm a.txt; ln b.txt a.txt``, and the
    two names now share an inode. Keyed off that, the two chains became one: b's edit
    left the surface, reverting "a.txt" wrote A's prior bytes into b.txt, and b's rows
    were marked reverted, so B's own prior was unreachable. ``replaced_by_a_link`` did
    not catch it, because a hard link is not a symlink.

The same recorded fact is what keeps a chain WHOLE when the file is deleted: two
spellings of one file stay one chain afterwards, where a live question had nothing left
to ask and split them back into two rows — one of which then offered a state ADDISON
wrote as "the way it was before Addison changed it". Rows written before the key existed
fall back to the live question and to what that leaves; docs/KNOWN-GAPS.md records it.

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

#: WHERE THE WRITE LANDED, minted by ``revert_key`` at write time and stored in the undo
#: payload beside the path. It is what lets "are these two rows the same file?" be a
#: question about two recorded facts rather than about the disk as it is now — see the
#: module docstring for the harm the second one does.
#:
#: No migration and no dataclass change, exactly as ``wrote_sha256`` before it: the
#: column is TEXT holding JSON, so a row written before this key simply lacks it and the
#: reader falls back to asking the disk, which is what those rows always did.
IDENTITY_KEY = "wrote_ident"

#: The prefix ``revert_key`` puts on an identity the OS could actually answer for.
#: Without it the value is the fallback — "there is nothing at that name" — and the two
#: are not interchangeable: ``another_file_stands_there`` must refuse a name that reaches
#: the WRONG file and must wave through one that reaches no file at all, or a file
#: Addison created and the person deleted could never be put back.
_ON_DISK = "file:"

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
#: A DIFFERENT FILE is at the name now — the hard-link sibling of the refusal above, and
#: worded apart from it because what a person needs told is not "a shortcut" but that the
#: file they are looking at is no longer the one Addison changed. It carries the same
#: "nothing was changed" for the same reason ``_LINK_STANDS_THERE_WRITE`` does.
#:
#: PUBLIC, where its symlink sibling is private, because TWO mechanisms refuse this and
#: they must not drift: this module's Revert, and ``WriteProjectFileTool.undo()``, which
#: reaches the same shell method from the chat header. One sentence with one owner —
#: a second copy would be a second thing to correct.
ANOTHER_FILE_STANDS_THERE_WRITE = (
    "A different file is at that name now, so Addison won't put the old text there. "
    "Nothing was changed."
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
    #: Every FILE this chain's writes landed on (``IDENTITY_KEY``). EVIDENCE, never
    #: display and never on the wire: ``another_file_stands_there`` compares what is at
    #: the name now against this set, so a chain can be put back onto a file it actually
    #: wrote and onto no other.
    #:
    #: Usually one. More than one when the file at a name was REPLACED between two writes
    #: — an editor that saves by rename, ``git checkout`` — which is a new file at the
    #: same name and still one chain, because the name is the other thing that joins
    #: rows. A legacy row contributes the identity read while this list was being built,
    #: which is why the check cannot fire for one.
    identities: frozenset[str]


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
    """WHICH FILE this name reaches — **asked of the filesystem, never inferred from the
    spelling**. ``file:{st_dev}:{st_ino}`` when the OS can still find something at that
    name, ``path:{normpath(realpath(...))}`` when it cannot. Never crosses to the shell
    (see ``FileEdit.path``).

    ONE FUNCTION, THREE CALLERS, and they are three uses of the same question rather than
    three questions. ``write_project_file`` calls it to MINT the identity it records
    (``IDENTITY_KEY``); this module calls it to ask what a name reaches NOW
    (``another_file_stands_there``); and ``_read`` calls it for rows that recorded no
    identity, which is what those rows always did. An identity minted by one spelling of
    the question and checked by another is an identity that drifts, and the drift would
    be silent in the direction that writes bytes.

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

    Asking the OS is right in both: one inode reached twice is one answer, two inodes are
    two. Where two names reach one file TODAY — a hard link — that is what this reports,
    which is the true answer to the question asked and NOT on its own a reason to put two
    chains together. Whether they were one file WHEN ADDISON WROTE THEM is a different
    question, and ``_read`` answers it from ``IDENTITY_KEY``. This function used to be the
    whole of the grouping, and the endorsement that stood in this paragraph — hard links
    collapse "for the same reason they should" — was sound only while the two names were
    already one file at write time, which nothing checked (module docstring).

    ``lstat``, never ``stat``: the question is which directory entry this NAME reaches,
    and following a symlink that appeared after the write would move the identity onto a
    file confinement never saw. (What that same appearance must NOT move is the path
    handed to the shell — that is ``FileEdit.path``, which is recorded, not resolved.)

    WHEN THERE IS NOTHING TO ASK ABOUT — a file Addison created and the person deleted,
    the ordinary case, and the reason this cannot simply be "stat or nothing" — the
    stored path is the answer and it is compared EXACTLY. Not casefolded: a wrong merge
    writes bytes into a file nobody named.

    A wrong SPLIT is not free either, and this docstring used to say it was ("only two
    rows where one would do — and both are then reverted to real earlier states"). It is
    the S1/S2 resurrection arrived at from the other end: a split leaves a row behind
    whose prior is an intermediate state ADDISON wrote, the surface offers it as "the way
    it was before Addison changed it", and the next "Undo last action" writes it back.
    That is why deleting a file no longer splits its chain — rows carrying
    ``IDENTITY_KEY`` are grouped on what they recorded, and this fallback is reached only
    for rows written before it existed. docs/KNOWN-GAPS.md records that residual.

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
        return f"{_ON_DISK}{entry.st_dev}:{entry.st_ino}"
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


def another_file_stands_there(path: str, identities: frozenset[str]) -> bool:
    """Does this NAME reach a file OTHER than one this chain wrote? Then no write of ours
    may land on it.

    THE HARD-LINK SIBLING of ``replaced_by_a_link``, and the reason that check alone was
    not enough: ``os.path.islink`` is False for a hard link, so ``rm a.txt; ln b.txt
    a.txt`` left a written name reaching a different file with nothing refusing, and a
    revert wrote one file's prior bytes into the other. A link and a hard link are the
    same act — something else now stands where Addison wrote — and only one of them was
    being asked about.

    THREE ANSWERS, and the middle one is the one to get right:

      * NOTHING is at the name (``revert_key`` fell back to the stored spelling): allow.
        A file Addison created and the person deleted must still revert, and the revert
        is what puts it back — the same case ``replaced_by_a_link`` waves through.
      * The name reaches a file this chain WROTE: allow. More than one identity is
        ordinary rather than suspicious — an editor that saves by rename, or ``git
        checkout``, replaces the file at a name between two writes — and putting a chain
        back onto the file it most recently wrote is exactly the ask.
      * Anything else: refuse, and write nothing.

    THE COST, STATED. A file REPLACED since the last write and never written again — the
    person checked out another branch, then pressed Revert — is refused rather than
    overwritten. That is a tightening: the surface's "somebody has changed this since"
    warning covered the same ground with a warning, and this makes the one case where the
    file itself is gone a refusal. The BEFORE pane still holds the text to copy, which is
    the answer this module already gives for every partial revert.

    LEGACY ROWS CANNOT TRIGGER IT. A row from before ``IDENTITY_KEY`` contributes the
    identity read while the list was being built, so this compares that reading against
    itself. That is the honest answer for a row that never recorded where it landed —
    there is no write-time fact to hold it to — and docs/KNOWN-GAPS.md records what it
    leaves open."""
    if not identities:
        return False
    standing = revert_key(path)
    if standing is None or not standing.startswith(_ON_DISK):
        return False
    return standing not in identities


@dataclass(frozen=True)
class _Reading:
    """ONE read of the window: the chains it holds, and the two ways a name finds its own.

    Frozen and internal. It exists because grouping stopped being a dict comprehension
    over one key the moment the key became two recorded facts (name, identity) that have
    to be joined — and because the three public methods must all see the SAME reading, or
    the list, the diff and the revert would disagree about where a chain begins."""

    #: Newest-first within each chain, and the chains themselves in the order their newest
    #: row arrived — the order the table answered in, which is what the surface renders.
    chains: tuple[tuple[ActionSnapshot, ...], ...]
    #: Per chain, every identity its writes landed on. Same index as ``chains``.
    identities: tuple[frozenset[str], ...]
    #: Recorded path -> chain. One chain per name, and that is the JOIN's guarantee rather
    #: than an assumption: every row naming a path joins that name's component, so two
    #: chains cannot both hold it.
    by_path: dict[str, int]
    #: Recorded identity -> chain, unique for the same reason, and here for a caller whose
    #: spelling is not one of the recorded ones — an alias of the project folder, ``~``, a
    #: second name for one file.
    by_identity: dict[str, int]
    truncated: bool

    def index_for(self, path: str) -> int | None:
        """Which chain this NAME belongs to.

        THE RECORDED SPELLING FIRST, and the order is load-bearing. Asking the disk first
        would send a name whose file has been hard-linked onto another chain's file to
        THAT chain — which is the merge this whole design refuses, re-entered through the
        lookup. Matching the recorded string lands on the rows that name it, and
        ``another_file_stands_there`` then refuses the write rather than misdirecting it.

        The disk is asked only when no row recorded that spelling: the surface hands back
        the ``path`` it was given, but ``_edit_resolve`` resolves the folders a parameter
        names, so an alias of the project (or ``~``) arrives spelled differently and must
        still find its edit."""
        index = self.by_path.get(path)
        if index is not None:
            return index
        identity = revert_key(path)
        if identity is None:
            return None
        return self.by_identity.get(identity)


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
        reading = self._read(limit)
        return PendingEdits(
            edits=tuple(
                self._edit(chain, identities)
                for chain, identities in zip(reading.chains, reading.identities)
            ),
            truncated=reading.truncated,
        )

    def chain_for(self, path: str) -> FileEdit | None:
        """The one edit for ``path``, or ``None`` when Addison has no unreverted write
        to it. The diff's read — and it goes through exactly the same window and the
        same grouping as ``pending_edits``, so what the diff shows and what the list
        promised cannot come apart."""
        reading = self._read(_MAX_EDITS)
        index = reading.index_for(path)
        if index is None:
            return None
        return self._edit(reading.chains[index], reading.identities[index])

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
        reading = self._read(_MAX_EDITS)
        index = reading.index_for(path)
        if index is None:
            return FileRevertResult(ok=False, path=str(path), detail=_NOTHING_TO_PUT_BACK)
        chain = reading.chains[index]
        if self._shell_bridge is None:
            return FileRevertResult(ok=False, path=str(path), detail=_NO_SHELL)

        oldest = chain[-1]
        # The CONFINED value — see ``FileEdit.path``. ``_read`` admits no row whose
        # payload path is not a string, so this is one.
        target = oldest.undo_payload["path"]
        if replaced_by_a_link(target):
            return FileRevertResult(ok=False, path=target, detail=_LINK_STANDS_THERE_WRITE)
        # AND THE SAME QUESTION FOR THE OTHER KIND OF SWAP. A hard link is not a symlink,
        # so the check above answers False for it while the name reaches a different
        # file — and the shell's ledger holds the NAME, so it would let the write
        # through. Asked HERE rather than in the shell because the fact it compares
        # against is a row this process wrote.
        if another_file_stands_there(target, reading.identities[index]):
            return FileRevertResult(
                ok=False, path=target, detail=ANOTHER_FILE_STANDS_THERE_WRITE
            )
        existed = bool(oldest.undo_payload.get("existed"))
        # ``None`` means "there was nothing there", which the shell turns into a
        # delete — the same contract ``write_project_file.undo()`` uses, because it is
        # the same question asked of the same row.
        prior = (oldest.undo_payload.get("prior") or "") if existed else None
        # AFTER A RESTART, ASK TO BE LET BACK IN. The shell's write ledger dies with the
        # process while these rows do not, so every edit made before the last restart is
        # one this mechanism can describe perfectly and cannot put back. Adoption is the
        # narrow way back: the shell re-ledgers the path only if the bytes standing there
        # now hash to what the write recorded (``wrote_sha256``), which is a file Addison
        # wrote and nobody has touched since, so the restore below destroys no work of
        # anybody's, the one risk it carries.
        #
        # ASKED EVERY TIME rather than only when a ledger question says it is needed:
        # this process cannot know what the shell's ledger holds without asking, and a
        # question about the ledger followed by a write is two moments where one will do.
        # In a live session it is answered from the same digest the surface already shows
        # and changes nothing, the path is in the ledger either way.
        #
        # A REFUSAL IS NOT A FAILURE HERE. The bytes may legitimately differ (somebody
        # edited the file, which the surface has already warned about) or the shell may
        # be older than this method; the restore below then answers with the shell's own
        # ledger sentence, which is what it answered before adoption existed.
        self._adopt(target, chain[0].undo_payload.get("wrote_sha256"))
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
    def _adopt(self, target: str, wrote_sha256: object) -> None:
        """Ask the shell to take ``target`` back into its session write ledger, on the
        strength of the digest the write recorded. Best effort by design: the caller's
        next step is the restore, and the shell's own refusal is the honest answer to
        every way this can come back "no".

        A ROW WITH NO DIGEST asks nothing. Rows written before ``wrote_sha256`` existed
        have no proof to offer, and there is no weaker test this may fall back to, a
        path adopted without one would be a restore permission granted for bytes nobody
        checked, which is the widening this method exists instead of."""
        if self._shell_bridge is None or not isinstance(wrote_sha256, str) or not wrote_sha256:
            return
        adopt = getattr(self._shell_bridge, "adopt_workspace_path", None)
        if adopt is None:
            # An older shell, or a bridge double from before this method. Nothing to
            # recover, and the restore below is exactly what it always was.
            return
        try:
            adopt(target, wrote_sha256)
        except Exception:
            # Including the shell's own refusals: this is an attempt to IMPROVE the next
            # call's chances, so its failure may not become the sentence a person reads.
            return

    def _read(self, limit: int) -> _Reading:
        """One read of the window, joined into chains, each chain newest-first.

        ``limit + 1`` rows are asked for so "there are more" is a fact rather than a
        guess: a window that comes back exactly full is indistinguishable from a table
        that ends there, and the difference is what ``truncated`` reports.

        TWO ROWS ARE ONE CHAIN WHEN THEY SHARE A RECORDED FACT — the NAME they were
        written to, or the FILE they landed on (``IDENTITY_KEY``) — and "share" is
        transitive, which is why this is a join rather than a dict comprehension over one
        key. Both facts are about the past, so no later change to the filesystem can put
        two chains together (the hard link in the module docstring) or take one apart (a
        deleted file, whose two spellings had nothing left to ask about and split).

        A ROW THAT RECORDED NO IDENTITY asks the disk, exactly as every row did before the
        key existed, and the memo below keeps 200 rows of one much-written file to a
        single syscall — but its real job is that a file deleted between two lookups of
        the SAME string would otherwise answer differently for two rows of one chain and
        split it.

        TWO PASSES, and the order matters twice. The joining has to finish before any row
        is asked which chain it is in, because a later row can merge two components that
        were separate when an earlier one was placed. And the placing pass walks the rows
        in table order, so the chains come out ordered by their NEWEST row — the order the
        surface renders and the order ``pending_edits`` promises."""
        rows = self._store.unreverted_snapshots_for_tool(WRITE_TOOL_ID, limit + 1)
        truncated = len(rows) > limit

        # A union-find over LABELS: "name:<recorded path>" and "same file:<identity>". Two
        # namespaces, because a path and an identity are different kinds of fact and a
        # value of one must never be read as a value of the other.
        parent: dict[str, str] = {}

        def root(label: str) -> str:
            parent.setdefault(label, label)
            while parent[label] != label:
                parent[label] = parent[parent[label]]
                label = parent[label]
            return label

        def join(one: str, other: str) -> None:
            first, second = root(one), root(other)
            if first != second:
                parent[first] = second

        asked: dict[str, str | None] = {}
        usable: list[tuple[ActionSnapshot, str, str]] = []
        for row in rows[:limit]:
            raw = row.undo_payload.get("path")
            if not isinstance(raw, str):
                continue
            identity = row.undo_payload.get(IDENTITY_KEY)
            if not isinstance(identity, str) or not identity:
                if raw not in asked:
                    asked[raw] = revert_key(raw)
                identity = asked[raw]
                if identity is None:
                    continue
            join(f"name:{raw}", f"same file:{identity}")
            usable.append((row, raw, identity))

        chains: list[list[ActionSnapshot]] = []
        identities: list[set[str]] = []
        at: dict[str, int] = {}
        by_path: dict[str, int] = {}
        by_identity: dict[str, int] = {}
        for row, raw, identity in usable:
            component = root(f"name:{raw}")
            index = at.get(component)
            if index is None:
                index = at[component] = len(chains)
                chains.append([])
                identities.append(set())
            chains[index].append(row)
            identities[index].add(identity)
            # ``setdefault`` states what the join already guarantees — one chain per name
            # and per identity — instead of assuming it. Should that ever stop being true,
            # this keeps the FIRST answer, and rows arrive newest-first.
            by_path.setdefault(raw, index)
            by_identity.setdefault(identity, index)

        return _Reading(
            chains=tuple(tuple(chain) for chain in chains),
            identities=tuple(frozenset(found) for found in identities),
            by_path=by_path,
            by_identity=by_identity,
            truncated=truncated,
        )

    @staticmethod
    def _edit(chain: tuple[ActionSnapshot, ...], identities: frozenset[str]) -> FileEdit:
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
            identities=identities,
        )


def _reverted_sentence(path: str, *, deleted: bool) -> str:
    """What the person is told. The file's NAME, never its full path — the same rule
    every tool label and permission card follows."""
    name = os.path.basename(path) or path
    if deleted:
        return f"Removed {name} — Addison had created it."
    return f"Put {name} back the way it was before Addison changed it."
