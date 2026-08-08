"""The review surface's diff and per-file revert — Phase-3 plan Build §2 and §3.

``workspace.listEdits`` / ``readEditDiff`` / ``revertFile``, and the third mechanism
underneath them (``agent_core/snapshots/file_revert.py``): per-path, out-of-order,
chain-collapsing, redo-stack-untouching, ``write_project_file``-only.

Every test here names the mutation it kills (the repo's standard). The load-bearing
ones, in the order the plan raises them:

  * THE CHAIN. Reverting one snapshot of a multi-write file leaves a NEWER row
    unreverted whose prior is an intermediate state — so the next "Undo last action"
    writes back exactly what the person reverted away from. Both interleavings are
    driven here, and both must converge on the state the diff showed.
  * WRITE FIRST, MARK SECOND. A failed write marks nothing; a failed mark is
    idempotent and the next attempt converges. The reverse order is the one failure
    this mechanism must not have.
  * THE REDO STACK IS NOT TOUCHED. A pushed entry surfaces a "Do that again" control
    that always fails, because ``WriteProjectFileTool`` has no ``redo()``; clearing
    would discard a legitimately redoable undo belonging to another mechanism.
  * THE RESTART. The shell's write ledger dies with the process while the rows do not,
    so after a restart every historic edit must render as an honest sentence rather
    than a button that fails.
  * THE THREE-VALUED ANSWER. "Addison can't tell whether this changed since" is a real
    answer, and collapsing it into "unchanged" is what lets a revert silently discard
    somebody's own work.
"""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from agent_core.main import JsonRpcServer, build_registry
from agent_core.memory.store import Store
from agent_core.profiles import DEVELOPER
from agent_core.providers.base import ModelResponse, ModelRole, ProviderCapabilities
from agent_core.providers.router import ModelRouter
from agent_core.snapshots.file_revert import FileRevertManager, revert_key
from agent_core.snapshots.undo_manager import UndoManager
from agent_core.tools.base import ExecutionContext
from agent_core.tools.write_project_file import WriteProjectFileTool
from tests.conftest import ShellBridgeStubs, _FrameWriter, _PipeReader

_NOT_TRUSTED_SHELL_REFUSAL = "Addison can only undo a file change it made."


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- fakes -----------------------------------------------------------------


class _FakeWorkspaceShell(ShellBridgeStubs):
    """A shell with a DISK (a dict of path -> text) and a LEDGER (a set of paths it
    wrote this session), which are the only two pieces of state the revert path cares
    about — and which are separable here exactly as they are in the real shell.

    Separating them is the point: ``ledger`` can be emptied while ``disk`` stays full,
    which is precisely what a restart does and is otherwise unreachable in a test.

    ``case_insensitive`` MODELS THE VOLUME rather than assuming one. A dict keyed by the
    exact string is a case-SENSITIVE disk, and that is not the disk this product ships
    on: macOS's default volume is case-insensitive, so ``Notes.md`` and ``notes.md``
    reach ONE file there. A fake that could not express that is how a state-corruption
    bug survived a test suite that looked like it covered the case — the test asserted
    two spellings stay two files against a fake where they always would. Off by default,
    so every test written against distinct made-up paths is untouched; on for the ones
    that are ABOUT two spellings of one file, where it is set from what the real
    filesystem under ``tmp_path`` actually does."""

    def __init__(
        self,
        disk: dict[str, str] | None = None,
        ledger=None,
        case_insensitive: bool = False,
    ) -> None:
        self._case_insensitive = case_insensitive
        self.disk: dict[str, str] = {self._at(k): v for k, v in (disk or {}).items()}
        self.ledger: set[str] = set(
            self.disk if ledger is None else (self._at(path) for path in ledger)
        )
        self.restored: list[tuple[str, str | None]] = []
        self.viewed: list[str] = []
        self.asked_to_restore: list[list[str]] = []
        self.digested: list[list[str]] = []
        self.write_failure: str | None = None

    def _at(self, path: str) -> str:
        """Which NAME this is — the ledger's question, and the ledger's answer is a set
        lookup on the exact path it was given (``can_restore_workspace_paths`` "opens no
        file, stats no path"). Case is the only thing normalised here, so the two
        spellings of one file cannot get out of step the way two dict keys would."""
        return path.casefold() if self._case_insensitive else path

    def _on_disk(self, path: str) -> str:
        """Which BYTES this reaches — and a shortcut is followed, because the real shell
        follows one. ``read_workspace_view`` opens the path and ``restore_workspace_path``
        writes it, and neither does anything about a symlink standing there. A fake that
        treated a planted shortcut as an ordinary dict key would make the leak invisible:
        the test would prove the core refused and prove nothing about what refusing is
        FOR. Made-up paths (``/p/a.py``) are not links on any real filesystem, so every
        other test in this file is untouched."""
        return self._at(os.path.realpath(path) if os.path.islink(path) else path)

    # the write half (the tool's own path)
    def write_workspace_file(self, path: str, content: str) -> dict:
        at = self._on_disk(path)
        existed = at in self.disk
        prior = self.disk.get(at)
        self.disk[at] = content
        self.ledger.add(self._at(path))
        return {"existed": existed, "prior": prior}

    def restore_workspace_file(self, path: str, prior_content: str | None) -> None:
        # Recorded AS ASKED, never normalised: what crossed the boundary is exactly
        # what these tests are about.
        self.restored.append((path, prior_content))
        if self.write_failure is not None:
            raise RuntimeError(self.write_failure)
        # The NAME is what the ledger holds; the BYTES are wherever that name leads.
        if self._at(path) not in self.ledger:
            raise RuntimeError(_NOT_TRUSTED_SHELL_REFUSAL)
        at = self._on_disk(path)
        if prior_content is None:
            self.disk.pop(at, None)
        else:
            self.disk[at] = prior_content

    # the three questions the surface asks
    def can_restore_workspace_files(self, paths: list[str]) -> dict:
        self.asked_to_restore.append(list(paths))
        # Keyed by the path AS ASKED — the wire contract — and answered from the ledger,
        # which is a set of names.
        return {"restorable": {path: self._at(path) in self.ledger for path in paths}}

    def digest_workspace_files(self, paths: list[str]) -> dict:
        self.digested.append(list(paths))
        return {
            "digests": {
                path: (
                    {"sha256": _sha(self.disk[self._on_disk(path)]), "missing": False}
                    if self._on_disk(path) in self.disk
                    else {"sha256": None, "missing": True}
                )
                for path in paths
            }
        }

    def read_workspace_file_for_view(self, path: str) -> dict:
        self.viewed.append(path)
        if self._on_disk(path) not in self.disk:
            raise RuntimeError("That file isn't there.")
        content = self.disk[self._on_disk(path)]
        return {"content": content, "bytes": len(content.encode("utf-8")), "truncated": False}


class _ScriptedProvider:
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            native_tool_calling=True, max_context_tokens=100_000,
            supports_streaming=False, runs_off_device=False,
        )

    def send(self, messages, tools, effort=None, timeout=None, on_delta=None) -> ModelResponse:
        raise NotImplementedError


# --- helpers ---------------------------------------------------------------


class _Bench:
    """Store + the real write tool + both managers, with no server in the way.

    The snapshots are produced by the SHIPPING tool rather than hand-written rows, so
    every payload here — ``existed``, ``prior``, ``wrote_sha256`` — is the one the app
    actually records. A hand-built row would let this suite keep passing after the tool
    stopped writing the field the surface reads."""

    def __init__(self, tmp_path: Path, shell: _FakeWorkspaceShell) -> None:
        self.store = Store(tmp_path / "bench.sqlite3")
        self.shell = shell
        self.tool = WriteProjectFileTool(shell_bridge=shell)
        # The bridge goes into the REGISTRY's tools too: ``undo()`` gets no execution
        # context, so an undo through the UndoManager reaches the shell only through
        # the instance built here — and half of this suite is about what the two
        # mechanisms do to each other's rows.
        self.registry = build_registry(DEVELOPER, shell_bridge=shell)
        self.undo = UndoManager(store=self.store, tool_registry=self.registry)
        self.reverter = FileRevertManager(store=self.store, shell_bridge=shell)
        self._clock = 1_000

    def write(self, path: str, content: str):
        """One ``write_project_file`` call, recorded exactly as the orchestrator does."""
        self._clock += 1
        result = self.tool.execute(
            {"path": path, "content": content},
            ExecutionContext(conversation_id="c", shell_bridge=self.shell, resolved_path=path),
        )
        assert result.success and result.snapshot is not None
        # A distinct, increasing created_at per write. Real writes land in the same
        # second all the time and the rowid tiebreaker handles it — but a test whose
        # rows all share a timestamp is testing the tiebreaker, not the ordering, and
        # would stay green if the ORDER BY lost its first column.
        snapshot = replace(result.snapshot, created_at=self._clock)
        self.undo.record(snapshot)
        return snapshot

    def unreverted(self) -> list[str]:
        return [s.id for s in self.store.recent_unreverted_snapshots(limit=50)]


def _rpc(reader, writer, rid, method, params=None) -> dict:
    frame = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        frame["params"] = params
    reader.feed(frame)
    return writer.wait_for(lambda f: f.get("id") == rid and ("result" in f or "error" in f))


class _Harness:
    """A live server on fake pipes with a real store and the fake shell above.

    The data directory is a SIBLING of the project, never ``tmp_path`` itself: the floor
    refuses everything under the data dir, so a database at the top of ``tmp_path``
    would make every project path in the test untrusted for a reason unrelated to what
    is being asserted (the §1 suite's lesson)."""

    def __init__(
        self,
        tmp_path: Path,
        shell: _FakeWorkspaceShell,
        edits: tuple[tuple[str, str, int], ...] = (),
        legacy_paths: frozenset[str] = frozenset(),
        developer: bool = True,
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        db_path = data_dir / "app.sqlite3"
        self.shell = shell
        self.reader = _PipeReader()
        self.writer = _FrameWriter()
        self.seeded: dict[str, str] = {}
        #: How many times anything has asked the store for the trust rows. A handler
        #: that answers about a LIST must ask once, not once per row — see
        #: ``test_the_edits_list_reads_the_trust_rows_once``.
        self.trust_reads = 0

        def factory() -> Store:
            # Seeded HERE, on the WORKER thread, because a sqlite3 connection belongs
            # to the thread that opened it — the same reason the §1 suite plants its
            # trust row in the factory. The writes go through the REAL tool, so every
            # payload these tests read is the one the app records.
            store = Store(db_path)
            real_trust = store.list_workspace_trust

            def counted():
                self.trust_reads += 1
                return real_trust()

            # Counting the QUERY, not a call somewhere above it: the cost this guards
            # against is a store round trip on the worker thread, and the only place
            # that is observable is the store method itself.
            store.list_workspace_trust = counted  # type: ignore[method-assign]
            for path, content, at in edits:
                self.seeded[path] = self._seed(
                    store, path, content, at, legacy=path in legacy_paths
                )
            return store

        self.server = JsonRpcServer(
            reader=self.reader,
            writer=self.writer,
            tool_registry=build_registry(DEVELOPER, shell_bridge=shell),
            store_factory=factory,
            db_path=db_path,
            model_router=ModelRouter(configured={ModelRole.PRIMARY: _ScriptedProvider()}),
            shell_bridge=shell,
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self._rid = 0
        if developer:
            assert self.call("profile.set", {"profileId": "developer"})["mode"] == "open"

    def _seed(self, store: Store, path: str, content: str, at: int, legacy: bool) -> str:
        tool = WriteProjectFileTool(shell_bridge=self.shell)
        result = tool.execute(
            {"path": path, "content": content},
            ExecutionContext(conversation_id="c", shell_bridge=self.shell, resolved_path=path),
        )
        assert result.snapshot is not None
        payload = dict(result.snapshot.undo_payload)
        if legacy:
            # A row written BEFORE digests existed: the key is absent, not null. That
            # is the state the three-valued answer is derived from, and it is why old
            # installs are the reason `onDiskChanged` has a third value at all.
            payload.pop("wrote_sha256")
        snapshot = replace(result.snapshot, created_at=at, undo_payload=payload)
        store.insert_action_snapshot(snapshot)
        return snapshot.id

    def call(self, method: str, params: dict | None = None) -> dict:
        self._rid += 1
        return _rpc(self.reader, self.writer, self._rid, method, params or {})["result"]

    def trust(self, directory) -> None:
        assert self.call("workspace.grantTrust", {"directory": str(directory)})["ok"] is True

    def close(self) -> None:
        self.reader.close()
        self.thread.join(timeout=5)


@pytest.fixture
def project(tmp_path) -> Path:
    """A project directory and an ALIAS symlink pointing at it.

    pytest's ``tmp_path`` is already realpath'd, so a path built from it proves nothing
    about resolving (HANDOFF rigor lesson 11 — the reason step-5's own path tests could
    not see the mechanism they were about). Asking through ``tmp_path/"alias"`` proves
    both halves at once: that the handler resolves before it looks anything up, and
    that only the resolved value reaches the shell."""
    directory = tmp_path / "project"
    directory.mkdir()
    (tmp_path / "alias").symlink_to(directory)
    return directory


# ============================================================================
# THE DATA — write_project_file records what it wrote
# ============================================================================
def test_the_write_records_the_digest_of_what_it_put_on_disk(tmp_path):
    """``wrote_sha256`` is the whole basis of "has this changed since Addison wrote
    it". Without it the surface can only compare a file against itself, so every diff
    would attribute the person's own later edits to Addison — and Revert would throw
    them away with no warning.

    Mutation: drop the key from ``undo_payload``. Every ``onDiskChanged`` in this suite
    becomes ``None``, and the test below that asserts True/False fails."""
    shell = _FakeWorkspaceShell()
    bench = _Bench(tmp_path, shell)
    snapshot = bench.write("/p/a.py", "print(1)\n")

    assert snapshot.undo_payload["wrote_sha256"] == _sha("print(1)\n")
    # The digest is of the BYTES ON DISK: the shell writes these characters as UTF-8,
    # so hashing the argument and hashing the file must agree exactly.
    assert snapshot.undo_payload["wrote_sha256"] == _sha(shell.disk["/p/a.py"])
    # The undo contract is untouched — existed/prior are what they always were.
    assert snapshot.undo_payload["existed"] is False
    assert snapshot.undo_payload["prior"] is None


# ============================================================================
# THE COLLAPSE — N writes to one file are ONE edit
# ============================================================================
def test_three_writes_to_one_file_collapse_into_one_edit(tmp_path):
    """N writes, one thing to look at and one thing to press. BEFORE is the prior of
    the OLDEST unreverted write — where a revert lands — and the chain is newest-first.

    Mutation: take BEFORE from the newest row (``chain[0]``) and this reports an
    intermediate state; build the chain in ascending order and ``snapshot_ids``
    reverses. Both are invisible in the payload's shape and wrong on screen."""
    shell = _FakeWorkspaceShell(disk={"/p/a.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    first = bench.write("/p/a.py", "v1\n")
    second = bench.write("/p/a.py", "v2\n")
    third = bench.write("/p/a.py", "v3\n")
    bench.write("/p/other.py", "unrelated\n")

    pending = bench.reverter.pending_edits()
    assert len(pending.edits) == 2, "one edit per FILE, not per write"
    assert pending.truncated is False
    edit = next(e for e in pending.edits if e.path == "/p/a.py")
    assert edit.writes == 3
    assert edit.snapshot_ids == (third.id, second.id, first.id)
    assert edit.before == "v0\n", "BEFORE is the oldest prior, never an intermediate"
    assert edit.created is False
    assert edit.first_written_at == first.created_at
    assert edit.last_written_at == third.created_at
    # The digest is the NEWEST write's — what should be on disk if nobody else has
    # touched it. Taking it from the oldest would report every multi-write file as
    # changed the moment Addison wrote it twice.
    assert edit.wrote_sha256 == _sha("v3\n")


def test_a_created_file_reverts_by_being_removed(tmp_path):
    """``created`` is not decoration: the BEFORE pane is empty and Revert DELETES. The
    shell is told ``None``, the same contract ``undo()`` uses for the same row.

    Mutation: pass the empty prior (``""``) instead of ``None`` and a file Addison
    created is left behind empty rather than removed."""
    shell = _FakeWorkspaceShell()
    bench = _Bench(tmp_path, shell)
    bench.write("/p/new.md", "# hello\n")

    edit = bench.reverter.pending_edits().edits[0]
    assert edit.created is True and edit.before == ""

    result = bench.reverter.revert_path("/p/new.md")
    assert result.ok is True and result.deleted is True
    assert shell.restored == [("/p/new.md", None)]
    assert "/p/new.md" not in shell.disk


# ============================================================================
# THE CHAIN — the S1/S2 hazard, and both interleavings
# ============================================================================
def test_reverting_a_file_settles_the_whole_chain_so_undo_cannot_resurrect_it(tmp_path):
    """THE HAZARD, driven end to end. F is v0; Addison writes v1 (S1) then v2 (S2).
    Reverting S1 alone would leave disk at v0 with S2 still unreverted — and the next
    "Undo last action" would write S2's prior, v1, putting back exactly what the person
    reverted away from, with nothing noticing.

    Mutation: settle only the oldest row (``[chain[-1].id]``) or only the newest.
    Either leaves a row behind, ``undo_last`` finds it, and the last assertion sees v1
    back on disk."""
    shell = _FakeWorkspaceShell(disk={"/p/f.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    bench.write("/p/f.py", "v1\n")
    bench.write("/p/f.py", "v2\n")

    result = bench.reverter.revert_path("/p/f.py")
    assert result.ok is True
    assert shell.disk["/p/f.py"] == "v0\n"
    # ZERO unreverted rows for that path: the hazard evaporates by construction rather
    # than by care, which is the whole reason the chain is the unit of a revert.
    assert bench.unreverted() == []
    # ONE write, not two — replaying N undos would briefly put v1 on disk on the way.
    assert shell.restored == [("/p/f.py", "v0\n")]

    assert bench.undo.undo_last(1) == []
    assert shell.disk["/p/f.py"] == "v0\n", "the undo button must not resurrect v1"


def test_the_other_interleaving_converges_on_the_same_state(tmp_path):
    """The mirror: the person presses "Undo last action" FIRST (settling S2, disk back
    to v1) and reverts the file afterwards. The revert now sees only S1, targets v0 and
    lands there — the same place the other order reached.

    Mutation: compute the target from the NEWEST unreverted row's prior. This ends at
    v1 while the diff pane the person was reading said v0."""
    shell = _FakeWorkspaceShell(disk={"/p/f.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    first = bench.write("/p/f.py", "v1\n")
    bench.write("/p/f.py", "v2\n")

    assert [r.success for r in bench.undo.undo_last(1)] == [True]
    assert shell.disk["/p/f.py"] == "v1\n"
    assert bench.unreverted() == [first.id]

    # The diff shows v0 as BEFORE, and the revert must produce exactly that.
    assert bench.reverter.pending_edits().edits[0].before == "v0\n"
    assert bench.reverter.revert_path("/p/f.py").ok is True
    assert shell.disk["/p/f.py"] == "v0\n"
    assert bench.unreverted() == []


def test_reverting_one_file_leaves_another_files_chain_alone(tmp_path):
    """Per-PATH, which is the other half of "out of order". Reverting A must not settle
    B's rows, or the surface would quietly clear edits the person never looked at.

    Mutation: drop the path filter in ``revert_path`` and B disappears from the list
    without its file ever being put back."""
    shell = _FakeWorkspaceShell(disk={"/p/a.py": "a0\n", "/p/b.py": "b0\n"})
    bench = _Bench(tmp_path, shell)
    bench.write("/p/a.py", "a1\n")
    kept = bench.write("/p/b.py", "b1\n")

    assert bench.reverter.revert_path("/p/a.py").ok is True
    assert shell.disk == {"/p/a.py": "a0\n", "/p/b.py": "b1\n"}
    assert bench.unreverted() == [kept.id]


# ============================================================================
# WRITE FIRST, MARK SECOND
# ============================================================================
def test_a_failed_write_marks_nothing_and_leaves_the_edit_listed(tmp_path):
    """A revert that could not write leaves the world exactly as it found it — the
    edits stay listed and stay live, which is the truth: the file did not change.

    Mutation: swap the order (mark, then write). The rows vanish from the list while
    the file still holds Addison's version, leaving the person with no way back and no
    record that there ever was one."""
    shell = _FakeWorkspaceShell(disk={"/p/f.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    written = bench.write("/p/f.py", "v1\n")
    shell.write_failure = "Addison couldn't undo that file change."

    result = bench.reverter.revert_path("/p/f.py")
    assert result.ok is False
    assert result.detail == "Addison couldn't undo that file change."
    assert result.snapshot_ids == ()
    assert bench.unreverted() == [written.id], "a failed write must mark nothing"
    assert shell.disk["/p/f.py"] == "v1\n"


def test_a_bug_in_the_write_path_never_puts_its_own_text_on_screen(tmp_path):
    """The shell's refusals are plain sentences and are shown as they are; ANYTHING
    ELSE is a bug, and a bug's text is not a sentence for a person. ``main._plain``
    draws exactly this line, and the first generated fixture of this payload caught the
    version that did not — what it wrote to the committed file was
    "'_FixtureEditBridge' object has no attribute 'restore_workspace_file'".

    Mutation: catch bare ``Exception`` and pass ``str(exc)`` through. The assertion
    below sees the AttributeError's text where a sentence should be."""

    class _BrokenShell(_FakeWorkspaceShell):
        def restore_workspace_file(self, path: str, prior_content: str | None) -> None:
            raise AttributeError("'_Thing' object has no attribute 'restore'")

    shell = _BrokenShell(disk={"/p/f.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    written = bench.write("/p/f.py", "v1\n")

    result = bench.reverter.revert_path("/p/f.py")
    assert result.ok is False
    assert result.detail == "Addison couldn't put that file back. Nothing was changed."
    assert "attribute" not in result.detail
    # And it is still a failed WRITE, so it marked nothing.
    assert bench.unreverted() == [written.id]


def test_a_failed_mark_is_idempotent_and_the_next_attempt_converges(tmp_path):
    """The other side of the asymmetry, and why it is the correct way round: a failed
    MARK leaves rows claiming unreverted for a file that already sits at its prior —
    recoverable, because re-reverting computes the same target and writes the same
    bytes.

    Mutation: report ``ok`` when the mark failed. The row stays in the list with
    nothing indicating anything is wrong, and nothing ever settles it."""
    shell = _FakeWorkspaceShell(disk={"/p/f.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    written = bench.write("/p/f.py", "v1\n")

    real_mark = bench.store.mark_snapshots_reverted
    attempts: list[tuple[str, ...]] = []

    def flaky(ids):
        attempts.append(tuple(ids))
        if len(attempts) == 1:
            raise RuntimeError("database is locked")
        real_mark(ids)

    bench.store.mark_snapshots_reverted = flaky  # type: ignore[method-assign]

    first = bench.reverter.revert_path("/p/f.py")
    assert first.ok is False
    assert first.detail.startswith("Addison put the file back")
    assert shell.disk["/p/f.py"] == "v0\n", "the file WAS put back"
    assert bench.unreverted() == [written.id], "and the row still says otherwise"

    second = bench.reverter.revert_path("/p/f.py")
    assert second.ok is True
    assert bench.unreverted() == []
    assert shell.disk["/p/f.py"] == "v0\n"
    # The same bytes both times. Idempotence here is not luck: the target is recomputed
    # from the same oldest prior, so a second attempt cannot land anywhere else.
    assert shell.restored == [("/p/f.py", "v0\n"), ("/p/f.py", "v0\n")]


def test_the_whole_chain_is_marked_in_one_statement(tmp_path):
    """ONE statement, ONE commit (the plan's words). A loop of single-row updates leaves
    a window in which a crash strands half a chain — the file already at its prior while
    rows still describe changes live on disk, which is the exact resurrection the chain
    semantics exist to make impossible.

    Mutation: implement ``mark_snapshots_reverted`` as a loop over
    ``mark_snapshot_reverted``. The UPDATE count becomes three."""
    shell = _FakeWorkspaceShell(disk={"/p/f.py": "v0\n"})
    bench = _Bench(tmp_path, shell)
    for version in ("v1\n", "v2\n", "v3\n"):
        bench.write("/p/f.py", version)

    updates: list[str] = []
    real_conn = bench.store._conn

    class _CountingConnection:
        """A proxy, because ``sqlite3.Connection.execute`` is read-only and cannot be
        patched in place. Everything is delegated; only the UPDATEs are counted."""

        def execute(self, sql, *args):
            if "action_snapshots" in sql and sql.strip().upper().startswith("UPDATE"):
                updates.append(sql)
            return real_conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(real_conn, name)

    bench.store._conn = _CountingConnection()  # type: ignore[assignment]
    try:
        assert bench.reverter.revert_path("/p/f.py").ok is True
    finally:
        bench.store._conn = real_conn

    assert len(updates) == 1, f"three rows must settle in one statement, saw {updates}"
    assert bench.unreverted() == []


def test_marking_nothing_executes_nothing(tmp_path):
    """An empty chain commits nothing — less a guard against ``IN ()`` than the honest
    answer: a revert that settled no rows has nothing to record.

    Mutation: drop the early return and this raises an OperationalError."""
    store = Store(tmp_path / "empty.sqlite3")
    store.mark_snapshots_reverted([])


# ============================================================================
# THE REDO STACK IS NOT TOUCHED
# ============================================================================
def test_revert_never_pushes_to_or_clears_the_redo_stack(tmp_path, project):
    """``WriteProjectFileTool`` has no ``redo()``, so a pushed entry would light up a
    "Do that again" control that fails every time it is pressed; and CLEARING would
    discard a legitimately redoable entry belonging to the other mechanism.

    DRIVEN THROUGH THE RPC, not the manager alone. The manager cannot reach the redo
    stack — it is handed no undo manager, which is the structural half asserted at the
    end — so the only place this can go wrong is the handler that has both in scope,
    and "clear the redo stack after a revert so the UI is consistent" is exactly the
    tidy-looking line somebody would add there. A test that called ``revert_path``
    directly would survive it.

    Mutation: ``self.undo_manager._redo_stack.clear()`` (or ``.append(...)``) in
    ``_workspace_revert_file``. Clearing fails the last two assertions; appending makes
    ``canRedo`` true for a chain that has no redo at all."""
    first = str(project / "a.py")
    second = str(project / "b.py")
    shell = _FakeWorkspaceShell(disk={first: "a0\n", second: "b0\n"})
    harness = _Harness(
        tmp_path, shell, edits=((first, "a1\n", 100), (second, "b1\n", 101))
    )
    try:
        harness.trust(project)
        # A real, legitimately redoable entry: undo_last pushes the row it settled.
        undone = harness.call("undo.undoLastAction")
        assert undone["ok"] is True and undone["canRedo"] is True

        assert harness.call("workspace.revertFile", {"path": first})["ok"] is True

        # Still exactly the entry the UNDO put there: not cleared, and nothing of the
        # revert's own added beside it.
        assert [s.id for s in harness.server.undo_manager._redo_stack] == [
            harness.seeded[second]
        ]
        # And over the wire, which is what the "Do that again" control reads.
        after = harness.call("undo.undoLastAction")
        assert after["detail"] == "There was nothing to undo."
        assert after["canRedo"] is True
        # The structural half: the manager has no route to the stack in the first place.
        assert not hasattr(harness.server.file_revert_manager, "_undo_manager")
    finally:
        harness.close()


# ============================================================================
# THE BOUND, THE GROUPING, AND THE INDEX BEHIND THEM
# ============================================================================
def test_the_list_is_bounded_and_says_when_it_is(tmp_path):
    """Retention deletes REVERTED rows only (owner decision 2026-08-08), so the
    unreverted subset is bounded by nothing on disk — which is why every read of it
    bounds itself, and why a bounded answer has to admit that it is one.

    Mutation: drop the ``+ 1`` in ``_grouped`` and ``truncated`` is always false, so a
    person cannot tell a complete list from a clipped one."""
    shell = _FakeWorkspaceShell()
    bench = _Bench(tmp_path, shell)
    for index in range(5):
        bench.write(f"/p/f{index}.py", "x\n")

    pending = bench.reverter.pending_edits(limit=3)
    assert len(pending.edits) == 3
    assert pending.truncated is True
    # Newest first: the three most recent writes, never the first three the table held.
    assert [edit.path for edit in pending.edits] == ["/p/f4.py", "/p/f3.py", "/p/f2.py"]

    assert bench.reverter.pending_edits(limit=50).truncated is False


def test_the_grouping_key_is_whatever_the_filesystem_says_those_two_names_are(tmp_path):
    """THE PROPERTY, stated once and true on either kind of volume: two paths share a
    revert key exactly when the OS says they are the same file.

    Both guesses are wrong on one volume each, which is why neither is used:

      * comparing the resolved SPELLING (what this did until 2026-08-08, under a comment
        asserting the opposite) splits ONE file into two chains on the case-INSENSITIVE
        volume this product ships on;
      * CASEFOLDING (``policy._canonical``, which KNOWN-GAPS flags) merges TWO files
        into one revert target on a case-sensitive one — and a revert writes bytes.

    ``os.path.samefile`` is the assertion for the same reason it is the implementation:
    it needs no knowledge of which volume ``tmp_path`` is on, so this runs unmodified on
    a case-insensitive Mac and a case-sensitive CI box and asserts the true thing on
    each.

    Mutations, all killed here: casefold the key (fails wherever ``samefile`` is False);
    compare the resolved path alone (fails wherever it is True); use ``stat`` on the
    resolved path rather than the name (the hard link below stops being one file only if
    the identity stops being the file's); drop ``st_dev`` (not killed here — that one is
    argued at the code, since a second volume is not reachable from a test)."""
    upper = tmp_path / "Notes.md"
    lower = tmp_path / "notes.md"
    upper.write_text("upper\n", encoding="utf-8")
    lower.write_text("lower\n", encoding="utf-8")

    one_file = os.path.samefile(upper, lower)
    assert (revert_key(str(upper)) == revert_key(str(lower))) is one_file, (
        "the key must say what the filesystem says"
    )
    # Two names that were never the same file stay apart on every volume.
    other = tmp_path / "other.md"
    other.write_text("other\n", encoding="utf-8")
    assert revert_key(str(other)) != revert_key(str(upper))
    # A HARD LINK is one file under two names, and writing either changes the same
    # bytes — so one chain is the right answer, and it falls out of asking rather than
    # having to be handled.
    link = tmp_path / "linked.md"
    os.link(other, link)
    assert revert_key(str(link)) == revert_key(str(other))


def test_two_spellings_of_one_file_are_one_chain_with_one_before(tmp_path):
    """THE HAZARD BUG 1 IS, end to end, on the volume the test is running on.

    ``Notes.md`` holds v0. Addison writes v1 under that spelling and v2 under
    ``notes.md``. On a case-insensitive volume that is ONE file: two chains would list
    two edits for it, and reverting them in the wrong order lands the file on v1 with v0
    unreachable — the resurrection this module's chain collapse exists to make
    impossible, arrived at through the front door.

    The fake shell is told which volume it is standing on, so its dict models the same
    file the real filesystem does. Real files exist here only because ``revert_key`` asks
    the OS about them; the bytes the assertions read are the shell's, as everywhere else
    in this suite.

    Mutation: group by the resolved path again. The case-insensitive arm sees two edits,
    the second revert writes v1, and the final assertion — that the file ends where the
    diff said it would — fails."""
    upper = str(tmp_path / "Notes.md")
    lower = str(tmp_path / "notes.md")
    (tmp_path / "Notes.md").write_text("v0\n", encoding="utf-8")
    one_file = os.path.exists(lower) and os.path.samefile(upper, lower)
    if not one_file:
        # A case-sensitive volume: give the other name a file of its own, so the arm
        # below is about two real files rather than one file and one absence.
        (tmp_path / "notes.md").write_text("w0\n", encoding="utf-8")

    shell = _FakeWorkspaceShell(
        disk={upper: "v0\n"} if one_file else {upper: "v0\n", lower: "w0\n"},
        case_insensitive=one_file,
    )
    bench = _Bench(tmp_path, shell)
    bench.write(upper, "v1\n")
    bench.write(lower, "v2\n")

    edits = bench.reverter.pending_edits().edits
    if one_file:
        assert len(edits) == 1, "one file on disk is one edit, however it was spelled"
        assert edits[0].writes == 2
        assert edits[0].before == "v0\n", "the OLDEST prior, across both spellings"
        # Either spelling names the same edit, because either spelling reaches the file.
        assert bench.reverter.chain_for(lower) == edits[0]

        assert bench.reverter.revert_path(lower).ok is True
        assert shell.disk[upper.casefold()] == "v0\n"
        # ZERO rows left: nothing survives to write v1 back over it.
        assert bench.unreverted() == []
        # And the second press has nothing to do rather than something wrong to do.
        assert bench.reverter.revert_path(upper).ok is False
        assert shell.disk[upper.casefold()] == "v0\n"
    else:
        # A case-sensitive volume: two files, two chains, and merging them would put
        # one file's prior bytes into the other.
        assert len(edits) == 2
        assert {edit.before for edit in edits} == {"v0\n", "w0\n"}
        assert bench.reverter.revert_path(upper).ok is True
        assert shell.disk == {upper: "v0\n", lower: "v2\n"}


def test_two_names_the_filesystem_cannot_answer_for_are_never_merged(tmp_path):
    """The fallback, and the direction it errs in. Nothing is on disk at either name —
    a file Addison created and the person deleted, the ordinary case — so there is
    nothing to ask, and the stored path is the tiebreak. It is compared EXACTLY: a wrong
    merge writes one file's bytes into another, a wrong split only leaves two rows where
    one would do.

    Mutation: casefold the fallback (or reach for ``policy._canonical``). These two
    become one chain, and the second revert writes over a file nobody named.

    KNOWN-GAPS records the residual this leaves: on a case-insensitive volume, two
    spellings of a file that is no longer there stay two chains."""
    shell = _FakeWorkspaceShell(disk={"/p/Notes.md": "upper\n", "/p/notes.md": "lower\n"})
    bench = _Bench(tmp_path, shell)
    bench.write("/p/Notes.md", "upper edited\n")
    bench.write("/p/notes.md", "lower edited\n")

    paths = {edit.path for edit in bench.reverter.pending_edits().edits}
    assert paths == {"/p/Notes.md", "/p/notes.md"}

    assert bench.reverter.revert_path("/p/Notes.md").ok is True
    assert shell.disk == {"/p/Notes.md": "upper\n", "/p/notes.md": "lower edited\n"}


def test_only_write_project_file_rows_are_listed(tmp_path):
    """A per-file revert is meaningful only for a snapshot that names a file and holds
    the bytes that were there before it. Another tool's row here would be an edit
    nobody made, offering a revert nobody could perform.

    Mutation: drop the ``tool_id`` filter from ``unreverted_snapshots_for_tool``. The
    ``save_file`` row below appears as an edit with an empty BEFORE."""
    from agent_core.tools.base import ActionSnapshot

    shell = _FakeWorkspaceShell()
    bench = _Bench(tmp_path, shell)
    bench.write("/p/a.py", "v1\n")
    bench.store.insert_action_snapshot(
        ActionSnapshot(
            id="save-1",
            tool_call_id="c1",
            tool_id="save_file",
            undo_payload={"path": "/p/saved.txt", "created_file": "/p/saved.txt"},
            created_at=9_999,
        )
    )

    assert [edit.path for edit in bench.reverter.pending_edits().edits] == ["/p/a.py"]


def test_the_surfaces_query_uses_its_index_rather_than_scanning_the_table(tmp_path):
    """``action_snapshots`` had no index at all until this wave, and the subset this
    surface reads is the one retention no longer collects — so it grows for the life of
    an install and a full scan of it would sit behind every click.

    EXPLAIN QUERY PLAN is the only honest assertion available here: a timing test on a
    small table measures nothing, and asserting the CREATE INDEX text would prove only
    that the schema file says what it says.

    Mutation: delete the index from schema.sql. The plan becomes a SCAN."""
    store = Store(tmp_path / "planned.sqlite3")
    plan = store._conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT id, tool_call_id, tool_id, undo_payload, created_at, reverted "
        "FROM action_snapshots WHERE tool_id = ? AND reverted = 0 "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        ("write_project_file", 200),
    ).fetchall()
    detail = " ".join(row["detail"] for row in plan)
    assert "idx_action_snapshots_tool_reverted" in detail, detail
    assert "SCAN action_snapshots" not in detail, detail
    # And the ordering rides the index rather than sorting everything it matched.
    assert "TEMP B-TREE" not in detail.upper(), detail


# ============================================================================
# THE THREE-VALUED "changed since", AND THE RESTART
# ============================================================================
def test_the_list_reports_changed_unchanged_and_cannot_tell(tmp_path, project):
    """Three answers, and the third is the one that matters: ``null`` means Addison
    cannot tell, and the UI says so out loud. Collapsing it into ``false`` is the single
    wrong reading that costs somebody their own work, because ``false`` is what lets a
    revert proceed with no warning.

    Mutation: treat a missing ``wrote_sha256`` as ``""`` and compare (every legacy row
    reports CHANGED), or default ``onDiskChanged`` to False (the legacy row reports
    unchanged and reverts silently over whatever is there)."""
    changed = str(project / "changed.py")
    same = str(project / "same.py")
    legacy = str(project / "legacy.py")
    gone = str(project / "gone.py")
    shell = _FakeWorkspaceShell(disk={changed: "old\n", same: "old\n", legacy: "old\n"})
    harness = _Harness(
        tmp_path,
        shell,
        edits=(
            (changed, "addison wrote this\n", 100),
            (same, "addison wrote this\n", 101),
            (legacy, "addison wrote this\n", 102),
            (gone, "temporary\n", 103),
        ),
        legacy_paths=frozenset({legacy}),
    )
    try:
        harness.trust(project)
        # The person edits one of them, and deletes another.
        shell.disk[changed] = "and then I changed it\n"
        shell.disk.pop(gone)

        by_path = {edit["path"]: edit for edit in harness.call("workspace.listEdits")["edits"]}
        assert by_path[changed]["onDiskChanged"] is True
        assert by_path[same]["onDiskChanged"] is False
        assert by_path[legacy]["onDiskChanged"] is None
        assert by_path[gone]["missing"] is True
        assert by_path[gone]["onDiskChanged"] is None
        assert by_path[same]["missing"] is False
        # ONE batch question per answer, for the whole list — not one per file.
        assert shell.digested == [list(by_path)]
        assert shell.asked_to_restore == [list(by_path)]
    finally:
        harness.close()


def test_an_edit_is_not_revertable_after_a_restart_and_the_before_text_survives(
    tmp_path, project
):
    """THE RESTART PROBLEM. The shell's write ledger dies with the process; the rows do
    not. So the surface must ask before it offers, or it renders a Revert beside every
    historic edit and every one of them fails.

    An empty ledger over a full disk is exactly what a restart leaves behind, and it is
    unreachable in a test except by separating the two.

    Mutation: hard-code ``revertable: True``, or infer it from the row rather than
    asking the shell. The dead-button regression comes straight back."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        assert harness.call("workspace.listEdits")["edits"][0]["revertable"] is True

        shell.ledger.clear()   # the restart
        assert harness.call("workspace.listEdits")["edits"][0]["revertable"] is False
        # And the BEFORE text is still reachable, which is what makes the honest line
        # useful rather than merely honest: the earlier version is there to copy.
        assert harness.call("workspace.readEditDiff", {"path": path})["before"] == "v0\n"
    finally:
        harness.close()


def test_undo_last_action_answers_a_plain_sentence_after_a_restart(tmp_path, project):
    """The same honesty for the chat header's button, which has the same problem and
    hides it by accident today (``hasUndoableActions`` only flips on a live activity
    update — an accident the review surface removes by reading the database).

    Asked BEFORE anything is attempted, so nothing is written and nothing is marked.

    Mutation: delete the pre-check. The answer becomes "Couldn't undo the last action.
    You may need to reverse it yourself." — a failure report for something that was
    never going to work, with no explanation and a suggestion to do it by hand."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        shell.ledger.clear()   # the restart

        answer = harness.call("undo.undoLastAction")
        assert answer["ok"] is False
        assert answer["detail"] == (
            "Addison changed that file before the app was last restarted, so it can't "
            "put it back for you."
        )
        # NOTHING was attempted and nothing was marked, so the review surface can still
        # show the earlier version rather than having quietly consumed the row.
        assert shell.restored == []
        assert harness.call("workspace.listEdits")["edits"][0]["path"] == path
    finally:
        harness.close()


def test_undo_last_action_still_works_normally_when_the_ledger_holds_the_file(
    tmp_path, project
):
    """The other half, and the reason the pre-check is a QUESTION rather than a rule:
    with the ledger intact the button behaves exactly as it always did.

    Mutation: return True unconditionally from the pre-check. Undo stops working for
    every project-file edit, which no test asserting only the restart case would
    notice."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)

        answer = harness.call("undo.undoLastAction")
        assert answer["ok"] is True
        assert shell.disk[path] == "v0\n"
    finally:
        harness.close()


# ============================================================================
# THE WIRE — mode gate, resolution, and what crosses to the shell
# ============================================================================
def test_all_three_methods_refuse_outside_the_developer_profile(tmp_path, project):
    """The mode gate, load-bearing for ``listDirectory``'s reason: trust rows persist
    and nothing revokes them on a profile switch. These payloads carry file paths, file
    TEXT and a WRITE, so the gate is first in every handler.

    Mutation: drop the ``self._mode() is PolicyMode.OPEN`` check in any of the three.
    Simple can then list Addison's edits, read a file's contents through the diff, or
    revert one."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        assert harness.call("workspace.listEdits")["edits"], "Developer sees the edit"

        assert harness.call("profile.set", {"profileId": "simple"})["mode"] == "safe"
        refusal = (
            "Looking inside your folders is part of the Developer profile. Switch to "
            "it in Settings to browse them here."
        )
        for method in ("workspace.listEdits", "workspace.readEditDiff", "workspace.revertFile"):
            answer = harness.call(method, {"path": path})
            assert answer["ok"] is False, method
            assert answer["error"] == refusal, method
        # And nothing was written, read or even asked about.
        assert shell.restored == [] and shell.viewed == []
        assert shell.disk[path] == "v1\n"
    finally:
        harness.close()


def test_the_diff_reads_before_from_the_database_and_after_from_disk(tmp_path, project):
    """The two panes answer different questions on purpose: the left is what Addison
    found (captured atomically at write time, in the table) and the right is what is
    there NOW — including whatever the person has done since, which is exactly what
    Revert would replace.

    Mutation: read BEFORE from disk too (both panes become identical and every diff is
    empty), or answer AFTER from the row instead of the file (the person's own edits
    vanish from the one screen built to show them)."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100), (path, "v2\n", 101)))
    try:
        harness.trust(project)
        shell.disk[path] = "v2 plus my own line\n"

        diff = harness.call("workspace.readEditDiff", {"path": path})
        assert diff["path"] == path
        assert diff["before"] == "v0\n", "the oldest prior — where Revert lands"
        assert diff["after"] == "v2 plus my own line\n"
        assert diff["beforeTruncated"] is False and diff["afterTruncated"] is False
    finally:
        harness.close()


def test_a_deleted_file_diffs_against_nothing_rather_than_refusing(tmp_path, project):
    """A file that is GONE is a diff, not a refusal: BEFORE against nothing, which is
    exactly what happened to it. Refusing would withhold the one thing still worth
    having — the earlier text, to copy — at the moment it is all that is left.

    The read error is only read as "gone" when the SHELL says so; anything else stays a
    plain sentence, because an empty right pane caused by an unreachable shell would be
    a lie in the shape of a deletion.

    Mutation: return the read error unconditionally. The person clicking the row the
    list marked ``missing`` gets "That file isn't there." and no way to the text the app
    is holding for them."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        shell.disk.pop(path)   # the person deleted it

        diff = harness.call("workspace.readEditDiff", {"path": path})
        assert diff["before"] == "v0\n"
        assert diff["after"] == ""
        assert diff["afterTruncated"] is False

        # ...and a shell that simply cannot answer is NOT a deletion.
        class _MuteDigest(_FakeWorkspaceShell):
            def digest_workspace_files(self, paths: list[str]) -> dict:
                raise RuntimeError("Addison can't look at your files just now.")

        mute = _MuteDigest(disk={})
        harness.server._shell_bridge = mute  # type: ignore[assignment]
        harness.server.file_revert_manager._shell_bridge = mute
        refused = harness.call("workspace.readEditDiff", {"path": path})
        assert refused["ok"] is False
        assert refused["error"] == "That file isn't there."
    finally:
        harness.close()


def test_the_diff_and_the_revert_resolve_once_and_only_the_resolved_path_crosses(
    tmp_path, project
):
    """Asked through a SYMLINKED ALIAS of the project, which is the only way to see the
    mechanism at all: ``tmp_path`` is already realpath'd, so a test built from it would
    pass with the resolution deleted (HANDOFF rigor lesson 11).

    The shell must be asked for the RESOLVED path — the key the row holds and the key
    the shell's own ledger holds — never the spelling the caller happened to use.

    Mutation: hand ``params["path"]`` to the bridge instead of the group key. The
    recorded path becomes the alias, the real shell's ledger check fails, and a revert
    that should work reports that Addison never wrote the file."""
    real = str(project / "a.py")
    alias = str(tmp_path / "alias" / "a.py")
    shell = _FakeWorkspaceShell(disk={real: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((real, "v1\n", 100),))
    try:
        harness.trust(project)

        diff = harness.call("workspace.readEditDiff", {"path": alias})
        assert diff["path"] == real
        assert shell.viewed == [real], "the alias must never reach the shell"

        reverted = harness.call("workspace.revertFile", {"path": alias})
        assert reverted["ok"] is True and reverted["path"] == real
        assert shell.restored == [(real, "v0\n")]
        assert shell.disk[real] == "v0\n"
        # And the edit is gone from the list, because its chain is settled.
        assert harness.call("workspace.listEdits")["edits"] == []
    finally:
        harness.close()


# ============================================================================
# THE PATH THAT CROSSES — recorded at write time, never re-resolved
# ============================================================================
_SECRET = "-----BEGIN PRIVATE KEY-----\nnever read this\n"


def _plant_a_shortcut(at: str, pointing_at: str) -> None:
    """Replace a written path with a symlink out of the project — the swap, done for
    real rather than described. It needs no attacker to happen: moving a config file
    into a dotfiles folder and linking it back is an ordinary Tuesday."""
    Path(pointing_at).parent.mkdir(parents=True, exist_ok=True)
    Path(pointing_at).write_text(_SECRET)
    os.symlink(pointing_at, at)


def test_a_shortcut_planted_at_a_written_path_never_moves_the_diff_or_the_revert(
    tmp_path, project
):
    """MEMBERSHIP IS THE WHOLE CONFINEMENT on these three methods, so the value that
    crosses has to be the value that was confined — ``undo_payload["path"]``, exactly as
    ``WriteProjectFileTool.undo()`` uses it.

    The group key was ``realpath``'d at READ time instead, so a shortcut appearing at a
    written path afterwards moved every later call onto whatever it pointed at: the
    diff's shell read landed outside every trusted folder and the file's full text went
    to the webview. Membership had been decided on a fresh resolution of the stored
    value rather than on the value that was confined, which is not the same test.

    Mutations, one per assertion group:
      * hand the bridge a re-resolution of the stored path (``revert_key(...)``, or
        ``os.path.realpath``): ``path`` in the list becomes the file outside the
        project, ``root`` goes null, ``revertable`` goes false because the shell's
        ledger never held that name — and the diff ships the secret;
      * drop the ``replaced_by_a_link`` check: the confined NAME crosses, and the shell
        follows the shortcut on its own (``File::open`` does, ``fs::write`` does), so
        the leak comes back through the one door closing the first mutation left open;
      * resolve the PARAMETER fully in these two handlers (``_browse_resolve`` instead
        of ``_edit_resolve``): the click keys off where the shortcut points while the
        row keys off the name, so it finds no edit at all — the person is told Addison
        has no change in that file, which is false, and the guard above is never
        reached at all."""
    path = str(project / "config.yml")
    secret = str(tmp_path / "outside" / "id_rsa")
    # The secret is ON the fake disk, and the fake follows a shortcut exactly as the
    # shell does — so a mutation that reaches it does not merely skip a refusal, it puts
    # the bytes in the answer, which is what this is really about.
    shell = _FakeWorkspaceShell(disk={path: "v0\n", secret: _SECRET})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        assert harness.call("workspace.listEdits")["edits"][0]["path"] == path

        _plant_a_shortcut(path, secret)

        edit = harness.call("workspace.listEdits")["edits"][0]
        assert edit["path"] == path, "the recorded path, never where it now points"
        assert edit["revertable"] is True, "the shell's ledger holds the recorded name"
        # ``root`` is DISPLAY ONLY and it does go null here, because the comparator it
        # shares with the authorization path (``policy.path_is_within``) resolves both
        # sides — which is right where it decides anything and merely cosmetic where it
        # decides what to render. The row therefore shows its whole path, which for a
        # file that is not there any more is the more useful of the two answers.
        assert edit["root"] is None
        assert edit["relativePath"] == path

        diff = harness.call("workspace.readEditDiff", {"path": path})
        assert diff["ok"] is False
        assert diff["error"] == (
            "That file has been replaced by a shortcut to somewhere else, so Addison "
            "won't open it."
        )
        assert shell.viewed == [], "nothing was read, here or anywhere else"
        assert "PRIVATE KEY" not in str(diff)

        reverted = harness.call("workspace.revertFile", {"path": path})
        assert reverted["ok"] is False
        assert reverted["error"] == (
            "That file has been replaced by a shortcut to somewhere else, so Addison "
            "won't put it back. Nothing was changed."
        )
        assert shell.restored == [], "and nothing was written through it either"
        assert shell.disk[secret] == _SECRET, "no bytes of Addison's went through it"
        # The edit is still listed and settled by nothing: a refusal marks no rows.
        assert len(harness.call("workspace.listEdits")["edits"]) == 1
        assert Path(secret).read_text() == _SECRET
    finally:
        harness.close()


def test_the_restart_question_is_asked_about_the_path_the_undo_would_use(
    tmp_path, project
):
    """The same root cause on the chat header's Undo, where it produced a sentence that
    was simply false.

    ``_write_edit_lost_to_a_restart`` re-resolved the recorded path before asking the
    shell whether it could still put it back. Once a shortcut stood at that path the
    shell was asked about a name its ledger had never held, answered no, and the person
    was told "Addison changed that file before the app was last restarted" — with no
    restart anywhere in it. And it was permanent: the pre-check marks nothing, so the
    row stayed and the sentence came back every time.

    Mutation: resolve the path before asking (``revert_key(...)``). The answer below
    becomes the restart refusal, the undo never runs, and the file keeps Addison's
    version for ever."""
    path = str(project / "config.yml")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        _plant_a_shortcut(path, str(tmp_path / "outside" / "id_rsa"))

        answer = harness.call("undo.undoLastAction")
        assert answer["ok"] is True, answer
        assert "restarted" not in answer["detail"]
        # The undo itself is the LIFO mechanism and writes through the shell's ledger
        # exactly as it always has; what this test is about is which path was asked
        # about. (That the shell would follow a shortcut on a write is the shell's own
        # gap, and is recorded in KNOWN-GAPS rather than papered over here.)
        assert shell.restored == [(path, "v0\n")]
    finally:
        harness.close()


def test_an_edit_whose_trust_was_revoked_is_still_listed_and_still_revertable(
    tmp_path, project
):
    """Revoking trust does not un-change a file. The question this surface answers is
    "what has Addison changed that is still changed", so the row stays — with
    ``root: null`` and the whole path to name it by — and it stays revertable, because
    the shell's undo has never asked about trust either (its ledger is session, not
    trust).

    A live-trust check here would build the worst surface available: one that shows a
    change, offers to put it back, and refuses to show you what it is.

    Mutation: add ``_is_trusted_path`` to either handler. The edit disappears from the
    list, or the diff refuses to open."""
    path = str(project / "a.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)
        assert harness.call("workspace.revokeTrust", {"directory": str(project)})["ok"] is True

        edit = harness.call("workspace.listEdits")["edits"][0]
        assert edit["root"] is None
        assert edit["relativePath"] == path, "no root to be relative to — say it all"
        assert edit["revertable"] is True
        assert harness.call("workspace.readEditDiff", {"path": path})["before"] == "v0\n"
        assert harness.call("workspace.revertFile", {"path": path})["ok"] is True
        assert shell.disk[path] == "v0\n"
    finally:
        harness.close()


def test_the_list_carries_metadata_only_and_the_relative_path_the_ui_renders(
    tmp_path, project
):
    """METADATA ONLY, deliberately: before/after for twenty files is megabytes on one
    JSON line across two process boundaries, for a list whose only job is to let
    somebody pick one of them.

    Mutation: add ``before``/``after`` to the Edit payload. The field-set assertion
    fails here and the fixture drift test fails with it, which is why both exist."""
    nested = project / "src"
    nested.mkdir()
    path = str(nested / "app.py")
    shell = _FakeWorkspaceShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)

        edit = harness.call("workspace.listEdits")["edits"][0]
        assert set(edit) == {
            "path", "root", "relativePath", "snapshotIds", "writes", "created",
            "firstWrittenAt", "lastWrittenAt", "revertable", "onDiskChanged", "missing",
        }
        assert edit["relativePath"] == "src/app.py"
        assert edit["root"] == str(project)
    finally:
        harness.close()


def test_the_edits_list_reads_the_trust_rows_once_however_many_edits_there_are(
    tmp_path, project
):
    """ONE store round trip for the whole list, not one per row. ``_browse_entries``
    states this rule for its 500 entries — "a store round-trip per row would put 500
    queries on the worker thread behind every click, and every one of them would be
    asking the identical question" — and the edits list broke it for up to 200, on the
    same thread, behind the same kind of click.

    The count is the assertion because the count is the defect: nothing about the
    PAYLOAD changes when this goes wrong, so a test of the answer cannot see it.

    Mutation: call ``self._trusted_root_for(edit.path)`` inside ``_edit_payload`` again.
    The delta below becomes one per edit."""
    edits = tuple((str(project / f"f{index}.py"), "v1\n", 100 + index) for index in range(6))
    shell = _FakeWorkspaceShell(disk={path: "v0\n" for path, *_rest in edits})
    harness = _Harness(tmp_path, shell, edits=edits)
    try:
        harness.trust(project)

        before = harness.trust_reads
        listed = harness.call("workspace.listEdits")["edits"]
        assert len(listed) == 6, "six files, so six chances to ask six times"
        assert harness.trust_reads - before == 1, (
            f"one read for the whole list, saw {harness.trust_reads - before}"
        )
        # And the answer is unchanged by reading them once — every row still names the
        # root it sits under and renders relative to it.
        assert {edit["root"] for edit in listed} == {str(project)}
        assert sorted(edit["relativePath"] for edit in listed) == [
            f"f{index}.py" for index in range(6)
        ]
    finally:
        harness.close()


def test_a_file_addison_never_changed_is_refused_in_plain_language(tmp_path, project):
    """A path with no unreverted row of its own is not a revert target, however trusted
    its folder is — and the refusal says what is true (nothing of Addison's is standing
    in that file) rather than naming a mechanism.

    Mutation: fall back to "the newest edit" when the path matches nothing, and
    pressing Revert on one file puts a different one back."""
    path = str(project / "untouched.py")
    shell = _FakeWorkspaceShell(disk={path: "mine\n"})
    harness = _Harness(tmp_path, shell)
    try:
        harness.trust(project)
        reverted = harness.call("workspace.revertFile", {"path": path})
        assert reverted["ok"] is False
        assert reverted["error"] == (
            "Addison has no change of its own to put back for that file."
        )
        assert shell.restored == []

        diff = harness.call("workspace.readEditDiff", {"path": path})
        assert diff["ok"] is False
        assert diff["error"] == (
            "Addison hasn't made a change to that file that's still in place."
        )
    finally:
        harness.close()


def test_a_shell_that_cannot_answer_never_takes_the_listing_down_with_it(
    tmp_path, project
):
    """``revertable`` / ``onDiskChanged`` / ``missing`` are honesty affordances, and a
    listing that failed because one of them could not be computed would be the least
    honest outcome available. A shell that refuses both questions yields the cautious
    answers — not revertable, cannot tell — and the edit is still on screen.

    Mutation: let the RuntimeError out of ``_restorable_map`` / ``_digest_map``. One
    unreachable shell call empties the whole review surface."""
    path = str(project / "a.py")

    class _MuteShell(_FakeWorkspaceShell):
        def can_restore_workspace_files(self, paths: list[str]) -> dict:
            raise RuntimeError("Addison can't look at your files just now.")

        def digest_workspace_files(self, paths: list[str]) -> dict:
            raise RuntimeError("Addison can't look at your files just now.")

    shell = _MuteShell(disk={path: "v0\n"})
    harness = _Harness(tmp_path, shell, edits=((path, "v1\n", 100),))
    try:
        harness.trust(project)

        edit = harness.call("workspace.listEdits")["edits"][0]
        assert edit["path"] == path
        assert edit["revertable"] is False
        assert edit["onDiskChanged"] is None
        assert edit["missing"] is False
    finally:
        harness.close()
