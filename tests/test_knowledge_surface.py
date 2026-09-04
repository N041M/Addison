"""Your documents — the ``knowledge.*`` surface (phase 3).

``docs/plans/knowledge-retrieval-plan.md`` phase 3: add a document, see what is
indexed, see what changed on disk, remove one. Phase 1 built the index and phase 2
registered the tool; until this landed nothing could put a document into either.

What these tests are about, in the order they appear:

  (1) the LIST, and the four things "on disk" can be — computed live from one batched
      digest call, never stored, and never able to fail the list;
  (2) ADDING, which is a picker, a chunk/screen/embed run and then a write, and every
      way it can end: written, cancelled, already there, nothing in it, no local model;
  (3) RE-READING, which is the same picker pointed at the row — and refuses a
      different file rather than turning one document into another;
  (4) REMOVING, which takes the passages and their vectors with it;
  (5) EVERY METHOD ANSWERS IN BOTH PROFILES, because a document list is not a
      capability;
  (6) THREADS: the picker and the embedding run happen off the worker, and the thing
      that does them touches no store — structurally, because no runtime assertion
      can see a cross-thread SQLite access that happens to work.

Every test here was mutation-proven: the line it guards was broken and this test
watched to fail. The mutations are named in the docstrings.
"""

from __future__ import annotations

import ast
import hashlib
import sqlite3
from pathlib import Path

import httpx
import pytest

from agent_core.knowledge.index import NO_LOCAL_MODEL, NOTHING_TO_INDEX
from agent_core.knowledge.indexer import KnowledgeIndexer
from agent_core.memory.store import Store
from agent_core.rpc.knowledge import (
    _ALREADY_ADDED,
    _CANNOT_ADD_DOCUMENTS,
    _DIFFERENT_FILE,
    _NOT_IN_LIST,
)
from agent_core.shell_bridge import PICKER_CANCELLED
from tests.conftest import IPC_DB_NAME, ShellBridgeStubs, _shutdown, build_server

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_SRC = _REPO_ROOT / "agent_core" / "main.py"
_KNOWLEDGE_SRC = _REPO_ROOT / "agent_core" / "rpc" / "knowledge.py"

_TENANCY = "/Users/mira/Documents/Tenancy agreement.md"
_NOTES = "/Users/mira/Notes.txt"

# Long enough to chunk into more than one passage, so a re-index has something to
# replace and a vector count is a number rather than a 1.
_TEXT = ("The deposit shall be returned within ten working days. " * 40) + "\n\n" + (
    "Notice must be given in writing one calendar month before the end. " * 40
)
_SHORTER_TEXT = "The deposit shall be returned within five working days.\n"


# ---------------------------------------------------------------------------
# The harness: a fake shell (a picker and a digest call) and a real indexer
# ---------------------------------------------------------------------------


def _document(path: str, text: str = _TEXT, *, display_name: str | None = None) -> dict:
    """What the shell answers for one picked document."""
    raw = text.encode("utf-8")
    return {
        "path": path,
        "displayName": display_name or path.rsplit("/", 1)[-1],
        "byteSize": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content": text,
    }


class _DocumentBridge(ShellBridgeStubs):
    """The shell's half of the picker and of ``digestWorkspaceFiles``.

    A fake because there is no shell in this process and no files on disk — and what
    is under test is the CORE's half: which sentence comes back, what gets written,
    and what it makes of the digests it is handed."""

    def __init__(self, answers: list | None = None, digests: dict | None = None) -> None:
        self.answers = list(answers or [])
        self.digests = dict(digests or {})
        self.suggested: list[str | None] = []
        self.digest_calls: list[list[str]] = []

    def pick_knowledge_document(self, suggested_path: str | None) -> dict:
        self.suggested.append(suggested_path)
        assert self.answers, "the picker was opened more times than the test scripted"
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def digest_workspace_files(self, paths: list[str]) -> dict:
        self.digest_calls.append(list(paths))
        return {"digests": {p: self.digests[p] for p in paths if p in self.digests}}


def _indexer(*, down: bool = False, model: str = "test-embed") -> KnowledgeIndexer:
    """The REAL indexer over a mock transport — the real chunker, the real screening
    pass and the real vector encoding, with nothing on a network. ``down=True`` is a
    machine with no Ollama running, which is the failure owner decision 3 answers."""

    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})

    return KnowledgeIndexer(
        model=model,
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://local"),
    )


# The one ``type: ignore`` in this file, said once at the seam rather than at twenty
# call sites. ``build_server``'s ``bridge`` parameter is typed as the concrete
# ``IpcShellBridge``; a ``ShellBridgeStubs`` fake is structurally a bridge and not that
# class, which is the house pattern (see tests/test_routine_import.py).
_DEFAULT_EMBEDDER = object()


def _server(tmp_path, bridge=None, embedder=_DEFAULT_EMBEDDER):
    """The harness with a fake shell and, unless told otherwise, a working embedder."""
    return build_server(
        tmp_path,
        register_tool=False,
        bridge=bridge,  # type: ignore[arg-type]
        embedder=_indexer() if embedder is _DEFAULT_EMBEDDER else embedder,
    )


def _call(harness, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    harness.reader.feed(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )
    return harness.writer.wait_for(lambda f: f.get("id") == request_id and "result" in f)["result"]


def _developer(harness, request_id: int = 900) -> None:
    result = _call(harness, "profile.set", {"profileId": "developer"}, request_id)
    assert result["ok"] is True and result["mode"] == "open"


def _seed(store: Store, doc_id: str, path: str) -> None:
    """One registered-but-unindexed document, written straight to a Store.

    The way in for the tests whose subject is a list with a row in it on a server that
    has no picker to add one with."""
    store.add_knowledge_document(
        doc_id=doc_id, path=path, display_name=path.rsplit("/", 1)[-1],
        sha256="a" * 64, byte_size=10, added_at=1,
    )


def _rows(tmp_path, table: str) -> list[sqlite3.Row]:
    """Read the committed database from the TEST thread. The worker owns the
    connection the server writes through; this is a second, read-only look at what
    actually landed on disk.

    A missing table is no rows, and that is not a softened assertion: the store is
    built lazily on the worker, and the tests that assert "nothing was written"
    include the ones where the request never reached the worker at all. Anything that
    WROTE a row would have created the table on the way."""
    conn = sqlite3.connect(tmp_path / IPC_DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (1) The list, and what "on disk" can be
# ---------------------------------------------------------------------------


def test_the_list_is_newest_first_and_carries_every_field_the_panel_renders(tmp_path):
    """The panel renders a name, a muted path, one status sentence built from
    ``status``/``chunkCount``/``flaggedChunks``/``onDisk``, and its actions. Every one
    of those has to be on the row, and the newest document has to be at the top —
    somebody who has just added one looks at the top of the list.

    Mutation: drop the ``, rowid DESC`` tiebreak from
    ``Store.list_knowledge_documents`` — these two adds land in the same whole second,
    so the order becomes whatever SQLite returns, and this fails. (``added_at DESC``
    itself is proven at the store, in ``test_documents_come_back_newest_first``: here
    the tiebreak would mask a reversed sort.) Drop any key from
    ``_knowledge_wire_row`` — the key-set assertion fails."""
    bridge = _DocumentBridge([_document(_TENANCY), _document(_NOTES, _SHORTER_TEXT)])
    h = _server(tmp_path, bridge)
    try:
        assert _call(h, "knowledge.add", {}, 1)["ok"] is True
        assert _call(h, "knowledge.add", {}, 2)["ok"] is True
        documents = _call(h, "knowledge.list", {}, 3)["documents"]

        assert [d["path"] for d in documents] == [_NOTES, _TENANCY], (
            "newest first: the document just added belongs at the top"
        )
        assert set(documents[0]) == {
            "id", "displayName", "path", "status", "detail", "chunkCount",
            "flaggedChunks", "byteSize", "addedAt", "indexedAt", "onDisk",
        }
        assert documents[0]["displayName"] == "Notes.txt"
        assert documents[0]["status"] == "indexed"
        assert documents[0]["detail"] is None
        assert documents[0]["chunkCount"] >= 1
        assert documents[0]["byteSize"] == len(_SHORTER_TEXT.encode("utf-8"))
        assert documents[0]["indexedAt"] is not None
    finally:
        _shutdown(h.reader, h.thread)


def test_the_list_never_carries_the_digest_it_compared(tmp_path):
    """``sha256`` answers exactly one question — has this changed? — and this side
    answers it. A digest of somebody's private file has no business travelling to the
    webview as well.

    Mutation: add ``"sha256": row["sha256"]`` to ``_knowledge_wire_row`` — this
    fails."""
    bridge = _DocumentBridge([_document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        _call(h, "knowledge.add", {}, 1)
        documents = _call(h, "knowledge.list", {}, 2)["documents"]
        assert "sha256" not in documents[0]
        # Not vacuous: the digest IS stored, which is what makes onDisk answerable.
        assert _rows(tmp_path, "knowledge_documents")[0]["sha256"]
    finally:
        _shutdown(h.reader, h.thread)


def test_on_disk_is_computed_live_from_one_batched_digest_call(tmp_path):
    """All four answers, from ONE call over every path — same, changed, missing, and
    the honest unknown for a path the shell would not judge (too big, unreadable,
    inside Addison's own data directory: the shell answers ``sha256: null``).

    Mutation: invert the comparison in ``_knowledge_on_disk``
    (``found != indexed_digest``) — same and changed swap and this fails. Delete the
    ``entry.get("missing")`` branch — the missing row reads "changed" instead. Ask
    the shell per row instead of once — the ``digest_calls`` assertion fails."""
    third = "/Users/mira/Recipes.md"
    fourth = "/Users/mira/Diary.md"
    documents = [
        _document(_TENANCY), _document(_NOTES, _SHORTER_TEXT),
        _document(third), _document(fourth),
    ]
    bridge = _DocumentBridge(list(documents))
    h = _server(tmp_path, bridge)
    try:
        for i in range(4):
            assert _call(h, "knowledge.add", {}, i + 1)["ok"] is True
        bridge.digest_calls.clear()
        bridge.digests = {
            _TENANCY: {"sha256": documents[0]["sha256"], "missing": False},
            _NOTES: {"sha256": "f" * 64, "missing": False},
            third: {"sha256": None, "missing": True},
            fourth: {"sha256": None, "missing": False},
        }

        listed = {d["path"]: d["onDisk"] for d in _call(h, "knowledge.list", {}, 9)["documents"]}
        assert listed == {
            _TENANCY: "same",
            _NOTES: "changed",
            third: "missing",
            fourth: "unknown",
        }
        assert len(bridge.digest_calls) == 1, "one batched call for the whole list"
        assert sorted(bridge.digest_calls[0]) == sorted([_TENANCY, _NOTES, third, fourth])
    finally:
        _shutdown(h.reader, h.thread)


def test_a_shell_that_cannot_answer_never_empties_the_list(tmp_path):
    """The list is what somebody opens the panel to see. A shell that refused the
    batch, or a bridge that timed out, must cost the four ``onDisk`` words and not the
    screen.

    Mutation: remove the ``except Exception`` around ``digest_workspace_files`` in
    ``_knowledge_on_disk`` — the raise reaches the worker and the answer becomes an
    error frame with no documents in it."""

    class _BrokenDigest(_DocumentBridge):
        def digest_workspace_files(self, paths: list[str]) -> dict:
            raise RuntimeError("Addison couldn't finish that just now. Please try again.")

    bridge = _BrokenDigest([_document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        _call(h, "knowledge.add", {}, 1)
        documents = _call(h, "knowledge.list", {}, 2)["documents"]
        assert len(documents) == 1
        assert documents[0]["onDisk"] == "unknown"
    finally:
        _shutdown(h.reader, h.thread)


def test_with_no_shell_at_all_the_list_still_answers(tmp_path):
    """The CLI harness and the fixture server have no bridge. "unknown" is the honest
    word for "nobody looked", and it is what the committed ``knowledge.list.json``
    fixture carries.

    The row is seeded into the database directly, because with no shell there is no
    picker and therefore no way to add one — and a list of nothing would never reach
    the code this is about.

    Mutation: drop BOTH the ``self._shell_bridge is None`` guard and the
    ``except Exception`` around the digest call in ``_knowledge_on_disk`` — the
    attribute call on None reaches the worker and the list stops answering. Either one
    alone leaves the other holding it up, and that is the point of having two: the
    guard is the cheap early-out for the shell-free case, and the catch-all is what
    makes "the list never fails" true whatever the shell does."""
    seed = Store(tmp_path / IPC_DB_NAME)
    seed.set_setting("widgets_seeded", "1")
    _seed(seed, "doc-1", _TENANCY)
    h = _server(tmp_path)
    try:
        documents = _call(h, "knowledge.list", {}, 1)["documents"]
        assert [d["path"] for d in documents] == [_TENANCY]
        assert documents[0]["onDisk"] == "unknown"
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (2) Adding a document
# ---------------------------------------------------------------------------


def test_adding_writes_the_row_its_passages_and_their_vectors(tmp_path):
    """The happy path, checked against the DATABASE and not only against the reply: a
    document whose row landed without chunks would report itself indexed and answer
    questions from nothing, and one whose chunks landed without vectors would be
    invisible to every search.

    Mutation: delete the ``self.store.index_document(...)`` call in
    ``_knowledge_commit`` — the row survives as 'pending' and both the chunk and the
    vector assertions fail."""
    bridge = _DocumentBridge([_document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        answer = _call(h, "knowledge.add", {}, 1)
        assert answer["ok"] is True
        document = answer["document"]
        assert document["path"] == _TENANCY
        assert document["displayName"] == "Tenancy agreement.md"
        assert document["status"] == "indexed"

        rows = _rows(tmp_path, "knowledge_documents")
        assert len(rows) == 1 and rows[0]["id"] == document["id"]
        assert rows[0]["byte_size"] == len(_TEXT.encode("utf-8"))
        chunks = _rows(tmp_path, "knowledge_chunks")
        vectors = _rows(tmp_path, "knowledge_embeddings")
        assert len(chunks) == document["chunkCount"] >= 2
        assert len(vectors) == len(chunks), "one vector per passage, or the search sees none"
        assert {v["model"] for v in vectors} == {"test-embed"}
        # The picker was opened for an ADD, so it was pointed nowhere.
        assert bridge.suggested == [None]
    finally:
        _shutdown(h.reader, h.thread)


def test_a_cancelled_picker_writes_nothing_and_is_not_an_error(tmp_path):
    """Closing a dialog is a person changing their mind, not a failure — the panel
    shows nothing at all for it. Told apart from a refusal by the shell's own
    sentence, which is why that sentence is one shared constant.

    Mutation: drop the ``PICKER_CANCELLED`` branch in ``_knowledge_pick_and_prepare``
    — the answer becomes ``{ok: false, error: "You closed the picker..."}`` and the
    ``cancelled`` assertion fails."""
    bridge = _DocumentBridge([RuntimeError(PICKER_CANCELLED)])
    h = _server(tmp_path, bridge)
    try:
        assert _call(h, "knowledge.add", {}, 1) == {"ok": False, "cancelled": True}
        assert _rows(tmp_path, "knowledge_documents") == []
        assert _rows(tmp_path, "knowledge_chunks") == []
    finally:
        _shutdown(h.reader, h.thread)


def test_the_shells_own_refusal_is_relayed_untouched(tmp_path):
    """Too big, not an ordinary file, not UTF-8, inside Addison's data directory: the
    shell is the process that looked at the file, and it wrote each sentence for the
    person. Rewording it here would be a second copy to keep true.

    Mutation: replace ``str(exc)`` with a generic sentence in
    ``_knowledge_pick_and_prepare`` — this fails."""
    refusal = "That document is too big for Addison to add — it can take files up to 2 MB."
    bridge = _DocumentBridge([RuntimeError(refusal)])
    h = _server(tmp_path, bridge)
    try:
        assert _call(h, "knowledge.add", {}, 1) == {"ok": False, "error": refusal}
        assert _rows(tmp_path, "knowledge_documents") == []
    finally:
        _shutdown(h.reader, h.thread)


def test_the_same_document_cannot_be_added_twice(tmp_path):
    """``path`` is UNIQUE, so a second add is an integrity error waiting to happen.
    The refusal names the other control by the word on it, because "already added" on
    its own leaves somebody pressing the same button again.

    Mutation: delete the ``knowledge_document_at_path`` check in
    ``_knowledge_commit`` — the insert raises sqlite3.IntegrityError, the worker
    answers an error frame instead of a sentence, and this fails."""
    bridge = _DocumentBridge([_document(_TENANCY), _document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        assert _call(h, "knowledge.add", {}, 1)["ok"] is True
        assert _call(h, "knowledge.add", {}, 2) == {"ok": False, "error": _ALREADY_ADDED}
        assert len(_rows(tmp_path, "knowledge_documents")) == 1
    finally:
        _shutdown(h.reader, h.thread)


def test_a_document_with_no_text_in_it_leaves_no_row(tmp_path):
    """An empty file has nothing to retrieve and nothing a Try again would fix, so
    there is no row to leave behind — unlike the missing-model case below, which is
    the same exception type and gets the opposite treatment.

    Mutation: remove the ``NOTHING_TO_INDEX`` branch in
    ``_knowledge_pick_and_prepare`` — an empty document lands as a failed row and both
    assertions fail."""
    bridge = _DocumentBridge([_document(_NOTES, "   \n\n  \n")])
    h = _server(tmp_path, bridge)
    try:
        assert _call(h, "knowledge.add", {}, 1) == {"ok": False, "error": NOTHING_TO_INDEX}
        assert _rows(tmp_path, "knowledge_documents") == []
    finally:
        _shutdown(h.reader, h.thread)


def test_a_document_is_remembered_when_there_is_no_local_embedding_model(tmp_path):
    """Owner decision 3 (2026-08-24): refuse rather than embed in the cloud. So the
    document is REMEMBERED — the row exists, marked failed, carrying the sentence the
    panel prints verbatim — and pressing Try again once Ollama is running is all that
    is left to do. The answer is ``ok: true``, because the write succeeded and the row
    is what the panel has to render.

    Mutation: answer ``{ok: false, error: ...}`` instead of writing the row in
    ``_knowledge_commit``'s failure branch — the row-count assertion fails."""
    bridge = _DocumentBridge([_document(_TENANCY)])
    h = _server(tmp_path, bridge, embedder=_indexer(down=True))
    try:
        answer = _call(h, "knowledge.add", {}, 1)
        assert answer["ok"] is True
        document = answer["document"]
        assert document["status"] == "failed"
        assert document["detail"] == NO_LOCAL_MODEL.format(model="test-embed")
        assert document["chunkCount"] == 0
        assert document["indexedAt"] is None

        rows = _rows(tmp_path, "knowledge_documents")
        assert len(rows) == 1 and rows[0]["status"] == "failed"
        assert _rows(tmp_path, "knowledge_chunks") == []
    finally:
        _shutdown(h.reader, h.thread)


def test_with_no_embedder_wired_adding_says_so_plainly(tmp_path):
    """The CLI harness and any wiring that forgot the embedder. Not the "install
    Ollama" sentence — that one is true about the person's machine, and this is about
    Addison, so pointing at Ollama would send them to fix something that is not broken.

    Mutation: drop the ``embedder is None`` guard in ``_knowledge_pick_and_prepare``
    — the thread raises AttributeError and the answer becomes an error frame."""
    bridge = _DocumentBridge([_document(_TENANCY)])
    h = _server(tmp_path, bridge, embedder=None)
    try:
        assert _call(h, "knowledge.add", {}, 1) == {
            "ok": False, "error": _CANNOT_ADD_DOCUMENTS
        }
        assert _rows(tmp_path, "knowledge_documents") == []
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (3) Re-reading a document
# ---------------------------------------------------------------------------


def test_reindex_replaces_the_passages_and_their_vectors(tmp_path):
    """Update, after the file changed on disk. The old passages must GO: chunks left
    behind from a previous read answer questions from text that is no longer in the
    file, and their vectors would rank against the new ones.

    Mutation: change ``index_document``'s DELETE to a no-op — the "old ids are gone"
    assertion fails. Remove ``byte_size`` from its UPDATE — the size assertion
    fails."""
    bridge = _DocumentBridge([_document(_TENANCY), _document(_TENANCY, _SHORTER_TEXT)])
    h = _server(tmp_path, bridge)
    try:
        first = _call(h, "knowledge.add", {}, 1)["document"]
        old_chunk_ids = {row["id"] for row in _rows(tmp_path, "knowledge_chunks")}
        assert len(old_chunk_ids) >= 2

        answer = _call(h, "knowledge.reindex", {"id": first["id"]}, 2)
        assert answer["ok"] is True
        document = answer["document"]
        assert document["id"] == first["id"], "the same document, re-read"
        assert document["status"] == "indexed"
        assert document["detail"] is None
        assert document["byteSize"] == len(_SHORTER_TEXT.encode("utf-8"))
        assert document["indexedAt"] is not None

        new_chunks = _rows(tmp_path, "knowledge_chunks")
        new_ids = {row["id"] for row in new_chunks}
        assert not (old_chunk_ids & new_ids), "every passage of the old read must be gone"
        assert len(new_chunks) == document["chunkCount"]
        vectors = _rows(tmp_path, "knowledge_embeddings")
        assert {v["chunk_id"] for v in vectors} == new_ids, "the vectors follow the passages"

        stored = _rows(tmp_path, "knowledge_documents")[0]
        assert stored["sha256"] == hashlib.sha256(_SHORTER_TEXT.encode("utf-8")).hexdigest()
        assert stored["byte_size"] == len(_SHORTER_TEXT.encode("utf-8"))
        # The picker was pointed at the row's own path, so the dialog opens where the
        # document lives with its name filled in.
        assert bridge.suggested == [None, _TENANCY]
    finally:
        _shutdown(h.reader, h.thread)


def test_reindex_clears_the_failure_it_was_pressed_to_fix(tmp_path):
    """Try again, once Ollama is running. The row must come back clean: a ``detail``
    left standing under an 'indexed' status is a sentence the panel would keep showing
    about a document that is now fine.

    Mutation: drop ``detail = NULL`` from ``index_document``'s UPDATE — this fails."""
    bridge = _DocumentBridge([_document(_TENANCY)])
    h = _server(tmp_path, bridge, embedder=_indexer(down=True))
    try:
        failed = _call(h, "knowledge.add", {}, 1)["document"]
        assert failed["status"] == "failed" and failed["detail"]

        # The machine is fixed: a working embedder, and the same file picked again.
        h.server._embedder_ref = _indexer  # type: ignore[assignment]
        bridge.answers.append(_document(_TENANCY))
        document = _call(h, "knowledge.reindex", {"id": failed["id"]}, 2)["document"]
        assert document["status"] == "indexed"
        assert document["detail"] is None
        assert document["chunkCount"] >= 2
    finally:
        _shutdown(h.reader, h.thread)


def test_reindex_refuses_a_different_file_and_leaves_the_row_untouched(tmp_path):
    """The picker is pointed at the row's path, but a person can browse away from it.
    Writing what they picked would silently turn one document into another under a
    name they still recognise — and ``path`` is UNIQUE, so it would collide with the
    other row as well. Refused, naming the way to get what they probably wanted.

    Mutation: delete the ``row["path"] != path`` branch in ``_knowledge_commit`` — the
    document's stored path and passages change and this fails."""
    bridge = _DocumentBridge([_document(_TENANCY), _document(_NOTES, _SHORTER_TEXT)])
    h = _server(tmp_path, bridge)
    try:
        first = _call(h, "knowledge.add", {}, 1)["document"]
        before = _rows(tmp_path, "knowledge_documents")[0]
        before_chunks = {row["id"] for row in _rows(tmp_path, "knowledge_chunks")}

        assert _call(h, "knowledge.reindex", {"id": first["id"]}, 2) == {
            "ok": False, "error": _DIFFERENT_FILE
        }

        after = _rows(tmp_path, "knowledge_documents")
        assert len(after) == 1
        assert dict(after[0]) == dict(before), "the row is exactly as it was"
        assert {row["id"] for row in _rows(tmp_path, "knowledge_chunks")} == before_chunks
    finally:
        _shutdown(h.reader, h.thread)


def test_a_cancelled_re_read_changes_nothing(tmp_path):
    """Pressing Update and then closing the dialog leaves the document exactly as it
    was — including its 'changed on disk' state, which is still true.

    Mutation: write the row before the picker answers — impossible by construction
    today, which is the claim; the assertion below is the tripwire under it."""
    bridge = _DocumentBridge([_document(_TENANCY), RuntimeError(PICKER_CANCELLED)])
    h = _server(tmp_path, bridge)
    try:
        first = _call(h, "knowledge.add", {}, 1)["document"]
        before = dict(_rows(tmp_path, "knowledge_documents")[0])
        assert _call(h, "knowledge.reindex", {"id": first["id"]}, 2) == {
            "ok": False, "cancelled": True
        }
        assert dict(_rows(tmp_path, "knowledge_documents")[0]) == before
    finally:
        _shutdown(h.reader, h.thread)


def test_reindexing_something_that_is_no_longer_there_says_so(tmp_path):
    """Removed in another window, or by the person a moment ago. Answered before any
    picker opens, so nobody is asked to choose a file for a row that is gone.

    Mutation: return ``None`` instead of the refusal from
    ``_knowledge_reindex_target`` when the row is missing — the picker opens (and the
    scripted-answers assertion in the fake bridge fires)."""
    bridge = _DocumentBridge([])
    h = _server(tmp_path, bridge)
    try:
        assert _call(h, "knowledge.reindex", {"id": "no-such-document"}, 1) == {
            "ok": False, "error": _NOT_IN_LIST
        }
        assert _call(h, "knowledge.reindex", {}, 2) == {"ok": False, "error": _NOT_IN_LIST}
        assert bridge.suggested == [], "no dialog for a document that isn't there"
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (4) Removing a document
# ---------------------------------------------------------------------------


def test_removing_a_document_takes_its_passages_and_vectors_with_it(tmp_path):
    """The plan's phase 3 in one line: "Removal deletes its chunks and vectors in the
    same transaction." Counted, not assumed — an orphaned vector is invisible on every
    surface and still ranks against real passages in every search.

    Mutation: turn OFF ``PRAGMA foreign_keys`` in ``Store.__init__`` — the document
    row goes and the chunk and vector rows stay, and both counts fail."""
    bridge = _DocumentBridge([_document(_TENANCY), _document(_NOTES, _SHORTER_TEXT)])
    h = _server(tmp_path, bridge)
    try:
        first = _call(h, "knowledge.add", {}, 1)["document"]
        _call(h, "knowledge.add", {}, 2)
        kept = {row["id"] for row in _rows(tmp_path, "knowledge_chunks")
                if row["document_id"] != first["id"]}
        assert kept, "the other document's passages, which must survive"

        assert _call(h, "knowledge.remove", {"id": first["id"]}, 3) == {"ok": True}

        assert [row["id"] for row in _rows(tmp_path, "knowledge_documents")] != [first["id"]]
        assert len(_rows(tmp_path, "knowledge_documents")) == 1
        assert {row["id"] for row in _rows(tmp_path, "knowledge_chunks")} == kept
        assert {row["chunk_id"] for row in _rows(tmp_path, "knowledge_embeddings")} == kept
    finally:
        _shutdown(h.reader, h.thread)


def test_removing_something_that_is_no_longer_there_says_so(tmp_path):
    """Unlike ``mcp.remove``'s idempotent shrug: this is reached by clicking a row on
    the screen, so "there is nothing to remove" means the screen is stale and the
    person should be told.

    Mutation: answer ``{"ok": True}`` when ``remove_knowledge_document`` returns False
    — this fails."""
    h = _server(tmp_path)
    try:
        assert _call(h, "knowledge.remove", {"id": "gone"}, 1) == {
            "ok": False, "error": _NOT_IN_LIST
        }
        assert _call(h, "knowledge.remove", {}, 2) == {"ok": False, "error": _NOT_IN_LIST}
    finally:
        _shutdown(h.reader, h.thread)


def test_removing_a_document_mints_no_restore_point(tmp_path):
    """Owner decision 4 (2026-08-24): the three knowledge tables are EXCLUDED from
    snapshots, so a restore point taken here would not contain the document it is
    supposedly a way back from. Offering one would be worse than offering none — the
    panel says the removal is permanent, and it is.

    Mutation: add ``self._snapshot_auto("knowledge_remove")`` to ``_knowledge_remove``
    — the snapshot count grows and this fails."""
    bridge = _DocumentBridge([_document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        document = _call(h, "knowledge.add", {}, 1)["document"]
        before = len(_call(h, "snapshot.list", {}, 2)["snapshots"])
        assert _call(h, "knowledge.remove", {"id": document["id"]}, 3) == {"ok": True}
        assert len(_call(h, "snapshot.list", {}, 4)["snapshots"]) == before
    finally:
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# (5) Every method answers in BOTH profiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("developer", [False, True], ids=["simple", "developer"])
def test_every_knowledge_method_answers_in_both_profiles(tmp_path, developer):
    """The tool that searches these documents is LOW and read-only in both profiles
    (owner decision 2), so the surface that manages them is not a capability either —
    and hiding somebody's own document list when they switch to Simple is the failure
    the 2026-08-06 artifact decision reversed.

    Mutation: add ``if self._mode() is not PolicyMode.OPEN: return {"ok": False, ...}``
    to any handler in ``rpc/knowledge.py`` — the Simple half of this fails."""
    bridge = _DocumentBridge([_document(_TENANCY), _document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        if developer:
            _developer(h)
        added = _call(h, "knowledge.add", {}, 1)
        assert added["ok"] is True, added
        document_id = added["document"]["id"]
        assert _call(h, "knowledge.list", {}, 2)["documents"][0]["id"] == document_id
        assert _call(h, "knowledge.reindex", {"id": document_id}, 3)["ok"] is True
        assert _call(h, "knowledge.remove", {"id": document_id}, 4) == {"ok": True}
    finally:
        _shutdown(h.reader, h.thread)


def test_no_knowledge_handler_asks_which_mode_it_is_in(tmp_path):
    """The structural half of the test above, and the one that survives a handler
    nobody thought to add to it. ``rpc/knowledge.py`` may not consult the policy mode
    at all: there is no mode-dependent behaviour here to get right.

    Mutation: call ``self._mode()`` anywhere in ``rpc/knowledge.py`` — this fails,
    naming the line."""
    tree = ast.parse(_KNOWLEDGE_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("_mode", "_active_profile"):
            raise AssertionError(
                f"rpc/knowledge.py consults the policy mode at line {node.lineno}: "
                "knowledge.* answers in every mode, in both profiles"
            )


# ---------------------------------------------------------------------------
# (6) Threads: off the worker to read, on the worker to write
# ---------------------------------------------------------------------------


def test_the_picker_and_the_embedding_run_never_touch_the_store():
    """THE INVARIANT THAT NO RUNTIME ASSERTION CAN SEE. ``_knowledge_pick_and_prepare``
    runs on the ``knowledge-add`` thread so that a modal dialog and a minute-long
    embedding pass block neither loop. A ``sqlite3`` connection is usable only on the
    thread that opened it, and that thread is the worker — so a single ``self.store``
    in that method is a cross-thread database access, which in CPython usually raises
    but can also just work, intermittently, on somebody else's machine.

    So the rule is structural: the method that runs on the thread may not name the
    store, and the thread body in ``main.py`` may do exactly two things with what it
    gets back — put a commit job on the queue, or answer.

    Mutation: add ``self.store.list_knowledge_documents()`` to
    ``_knowledge_pick_and_prepare`` — this fails, naming the line."""
    tree = ast.parse(_KNOWLEDGE_SRC.read_text(encoding="utf-8"))
    prepare = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_knowledge_pick_and_prepare"
    )
    for node in ast.walk(prepare):
        if isinstance(node, ast.Attribute) and node.attr in ("store", "_store", "_ensure_built"):
            raise AssertionError(
                f"_knowledge_pick_and_prepare touches the store at line {node.lineno}: "
                "it runs on the knowledge-add thread, and the sqlite3 connection "
                "belongs to the worker"
            )
    # Not vacuous: it really is the method that calls the shell and the embedder.
    calls = {
        node.func.attr for node in ast.walk(prepare)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "pick_knowledge_document" in calls and "prepare" in calls


def test_the_thread_body_only_ever_enqueues_or_answers():
    """The other half: ``main.py``'s ``_run_knowledge_read`` is the thread, so
    anything it does itself is done off the worker. It may enqueue the commit job or
    answer the request, and nothing else.

    Mutation: call ``self._knowledge_commit(job)`` directly in ``_run_knowledge_read``
    instead of enqueueing it — this fails, naming the call."""
    tree = ast.parse(_MAIN_SRC.read_text(encoding="utf-8"))
    body = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_knowledge_read"
    )
    allowed = {
        "_knowledge_pick_and_prepare",  # the store-free read half
        "put",                          # the commit job, onto the worker's queue
        "_respond",
        "_respond_error",
    }
    for node in ast.walk(body):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr in allowed, (
                f"_run_knowledge_read calls {node.func.attr}() at line {node.lineno}, "
                "off the worker thread"
            )


def test_every_knowledge_method_but_add_is_routed_to_the_worker():
    """``main.py`` may name a ``knowledge.*`` method in exactly two places: the
    ``_KNOWLEDGE_JOBS`` table that routes it to the queue, and the ONE inline entry
    for ``knowledge.add``, whose handler starts a thread and returns. Anything else —
    an inline handler added by imitation of ``permission.respond`` — would put a store
    read on the read loop, silently, in a way no behavioural test of that handler
    would show.

    Mutation: move ``Method.KNOWLEDGE_LIST`` out of ``_KNOWLEDGE_JOBS`` and bind it to
    an inline handler — this fails, naming the method."""
    from agent_core import main as main_module
    from agent_core.protocol import Method

    named = {
        name for name, value in vars(Method).items()
        if isinstance(value, str) and value.startswith("knowledge.")
    }
    assert named, "no knowledge.* methods found in protocol.py — did they move?"
    assert {getattr(Method, name) for name in named} == (
        set(main_module._KNOWLEDGE_JOBS) | {Method.KNOWLEDGE_ADD}
    )
    assert Method.KNOWLEDGE_ADD not in main_module._KNOWLEDGE_JOBS

    tree = ast.parse(_MAIN_SRC.read_text(encoding="utf-8"))
    jobs_table = next(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "_KNOWLEDGE_JOBS" for t in node.targets)
    )
    allowed = {id(node) for node in ast.walk(jobs_table)}
    seen_add = 0
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Method"
            and node.attr in named
            and id(node) not in allowed
        ):
            if node.attr == "KNOWLEDGE_ADD":
                seen_add += 1
                continue
            raise AssertionError(
                f"main.py names Method.{node.attr} at line {node.lineno}, outside "
                "_KNOWLEDGE_JOBS: a knowledge.* method answered anywhere but the "
                "worker queue would read the store on the wrong thread"
            )
    assert seen_add == 1, "knowledge.add is bound inline exactly once"


def test_the_picker_thread_does_not_block_the_worker(tmp_path):
    """The reason for the whole split, asserted rather than argued: while a modal
    dialog is open, every other store RPC must still answer. This is the failure the
    folder picker had until 2026-08-22, where somebody browsing for a project held up
    the very panel they were browsing from.

    Mutation: route ``knowledge.add`` through ``_KNOWLEDGE_JOBS`` instead of the
    inline handler — the list below queues behind the picker and never arrives, and
    ``wait_for``'s deadline fires."""
    import threading

    opened = threading.Event()
    release = threading.Event()

    class _SlowPicker(_DocumentBridge):
        def pick_knowledge_document(self, suggested_path: str | None) -> dict:
            opened.set()
            assert release.wait(timeout=5), "the test never released the picker"
            return super().pick_knowledge_document(suggested_path)

    bridge = _SlowPicker([_document(_TENANCY)])
    h = _server(tmp_path, bridge)
    try:
        h.reader.feed({"jsonrpc": "2.0", "id": 1, "method": "knowledge.add", "params": {}})
        assert opened.wait(timeout=5), "the picker never opened"
        # The dialog is standing open. The worker must still be answering.
        assert _call(h, "knowledge.list", {}, 2) == {"documents": []}
        release.set()
        assert h.writer.wait_for(lambda f: f.get("id") == 1 and "result" in f)["result"]["ok"]
    finally:
        release.set()
        _shutdown(h.reader, h.thread)


# ---------------------------------------------------------------------------
# The store's own additions
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "knowledge-test.sqlite3")


def test_documents_come_back_newest_first(store: Store):
    """``added_at DESC`` proper, which the IPC test above cannot prove on its own: two
    documents added through the RPC land in the same whole second, so the ``rowid``
    tiebreak decides them and would mask a reversed sort. Here the seconds differ.

    Mutation: change ``ORDER BY added_at DESC`` to ``ASC`` in
    ``Store.list_knowledge_documents`` — this fails."""
    for index, path in enumerate([_TENANCY, _NOTES, "/Users/mira/Recipes.md"]):
        store.add_knowledge_document(
            doc_id=f"doc-{index}", path=path, display_name=path.rsplit("/", 1)[-1],
            sha256="a" * 64, byte_size=10, added_at=100 + index,
        )
    assert [row["id"] for row in store.list_knowledge_documents()] == [
        "doc-2", "doc-1", "doc-0"
    ]


def test_a_document_can_be_looked_up_by_id(store: Store):
    """Every caller of this is acting on a row somebody clicked, and the id is the
    only thing that survives a re-index.

    Mutation: return the first row regardless of the id (drop the WHERE clause) — the
    'not there' assertion fails."""
    _seed(store, "doc-1", _TENANCY)
    found = store.get_knowledge_document("doc-1")
    assert found is not None and found["path"] == _TENANCY
    assert store.get_knowledge_document("doc-2") is None


def test_a_document_can_be_looked_up_by_path(store: Store):
    """``path`` is UNIQUE, so this is "would adding this file be a duplicate?" asked
    before the insert that would raise.

    Mutation: drop the WHERE clause — the second assertion fails."""
    _seed(store, "doc-1", _TENANCY)
    found = store.knowledge_document_at_path(_TENANCY)
    assert found is not None and found["id"] == "doc-1"
    assert store.knowledge_document_at_path(_NOTES) is None


def test_indexing_records_the_size_of_what_it_just_read(store: Store):
    """A re-index reads the file AGAIN, and the file may have changed — that is the
    usual reason somebody presses Update. A stale size beside a fresh digest reads as
    a fact and is not one.

    Mutation: remove ``byte_size = COALESCE(?, byte_size)`` from ``index_document``'s
    UPDATE — the first assertion fails."""
    _seed(store, "doc-1", _TENANCY)
    rows = [{
        "id": "chunk-1", "ordinal": 0, "text": "hello", "char_start": 0, "char_end": 5,
        "flagged": 0, "screened_kinds": None, "vector": b"\x00\x00\x00\x00",
    }]
    store.index_document(
        doc_id="doc-1", sha256="b" * 64, rows=rows, model="m", dim=1, indexed_at=2,
        byte_size=4096,
    )
    written = store.get_knowledge_document("doc-1")
    assert written is not None and written["byte_size"] == 4096

    # ...and a caller with nothing new to say about the size leaves it alone rather
    # than being made to repeat it (the phase-1 and phase-2 tests index the same text
    # twice and never mention a size).
    store.index_document(
        doc_id="doc-1", sha256="c" * 64, rows=rows, model="m", dim=1, indexed_at=3
    )
    kept = store.get_knowledge_document("doc-1")
    assert kept is not None and kept["byte_size"] == 4096
