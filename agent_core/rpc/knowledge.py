"""knowledge.* handlers — the documents a person attached, and what Addison made of them.

Phase 3 of ``docs/plans/knowledge-retrieval-plan.md``: the surface. Phase 1 built the
index and phase 2 registered ``search_knowledge``; until now nothing could put a
document into either, so the tool's own "there are no documents to search yet"
sentence pointed at a Settings section that did not exist.

**ANSWERED IN EVERY MODE.** The tool that searches these documents is LOW and
read-only in both profiles (owner decision 2, 2026-08-24), so the surface that
manages them is not a capability either — and hiding somebody's own document list
when they switch to Simple is the failure the 2026-08-06 artifact decision reversed
(``docs/SAFETY.md`` owns that rule). Nothing here checks ``self._mode()``, and a test
holds that line.

**THE SHELL NEVER READS A DOCUMENT'S BYTES WITHOUT A PICKER IN BETWEEN**, and that
one rule shapes every method here. Adding reads through
``shell.pickKnowledgeDocument``. Re-reading — "Update" after the file changed on
disk, "Try again" after a failed index — opens the same picker again, pointed at the
file, and the person confirms. What the core stores is the PATH, so it can ask the
shell for a DIGEST later (``shell.digestWorkspaceFiles``, which already exists, is
path-based and never errors) and say honestly whether the file has changed since. It
never asks the shell to hand over CONTENT by path: that would give the Agent Core —
the middle-trust process with no OS permissions of its own — a read-any-file
capability the review surface deliberately confined to trusted roots. A persistent
shell-side consent ledger would be the way to buy the missing convenience; it is a
later option and not a gap.

**NEITHER LOOP MAY WAIT ON THIS.** The picker is modal and a local embedding run over
a two-megabyte document takes as long as it takes, so ``knowledge.add`` and the
re-read half of ``knowledge.reindex`` run on a thread of their own
(``main.py::_handle_knowledge_add``, on ``workspace.pickDirectory``'s precedent) and
hand the WRITE back to the worker as a ``knowledge_commit`` job. The split is why
``_knowledge_pick_and_prepare`` below touches no store: SQLite connections belong to
the thread that opened them, and that thread is the worker.

**NO RESTORE POINT IS TAKEN, EVER** — not on add, not on remove. The three knowledge
tables are excluded from snapshots (owner decision 4, 2026-08-24, on the
``tool_grants`` precedent), so a restore point would not contain a document anyway,
and minting one would promise a way back that does not exist.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from agent_core.knowledge.index import NOTHING_TO_INDEX, EmbeddingUnavailable
from agent_core.rpc.base import ServerContext
from agent_core.shell_bridge import PICKER_CANCELLED

# --- Frozen plain-language copy (CLAUDE.md: no jargon, personas 54/68) --------

#: Said when the picked file is already in the list. It names the OTHER control by
#: the word on it, because "already added" on its own leaves somebody pressing the
#: same button again.
_ALREADY_ADDED = "That document is already added. Use Update to read it again."

#: Said when the id no longer resolves — removed in another window, or gone by the
#: time a picker somebody left open was answered. NOT an error frame: a document
#: that is not there any more is an ordinary thing to find out.
_NOT_IN_LIST = "That document isn't in the list any more."

#: Said when Update or Try again came back with a different file. Replacing the row
#: would silently turn one document into another under a name the person still
#: recognises, so the answer is a refusal that names the way to get what they
#: probably wanted.
_DIFFERENT_FILE = "That's a different file. To add it as well, use Add a document."

#: Said when there is nothing in this process that can turn text into vectors — the
#: CLI harness, and any wiring that forgot the embedder. Not the "install Ollama"
#: sentence: that one is true about the person's machine, and this one is about
#: Addison, so pointing at Ollama would send them to fix something that is not broken.
_CANNOT_ADD_DOCUMENTS = "Addison can't add documents just now. Restart Addison and try again."

# --- What "on disk" can be. Computed live, never stored ----------------------
#
# NEVER A COLUMN. The answer is only true at the moment it is asked: a file changes
# while Addison is not looking, which is the entire reason this question exists. A
# stored answer would be a fact about a past moment rendered as a fact about now.
_SAME = "same"
_CHANGED = "changed"
_MISSING = "missing"
_UNKNOWN = "unknown"


class KnowledgeMixin(ServerContext):
    # --- the list ---------------------------------------------------------
    def _knowledge_list(self) -> dict:
        """knowledge.list -> ``{documents: [<row>]}``, newest first.

        ONE BATCHED DIGEST CALL for the whole list, never one per row: this answers a
        panel opening, and a round trip per document would put a Core -> Shell hop
        behind every line on the screen. The shell caps a batch at two hundred paths
        and refuses past that — which lands here as "Addison can't tell" for every
        row rather than as an error, because a list that will not load is worse than
        a list that will not judge. A knowledge base that large is not a shape
        anything here has seen; the day it is, this is the line to slice."""
        self._ensure_built()
        return {"documents": self._knowledge_wire_rows(self.store.list_knowledge_documents())}

    def _knowledge_wire_rows(self, rows: list[dict[str, Any]]) -> list[dict]:
        """Store rows -> wire rows, with ``onDisk`` resolved for all of them at once."""
        verdicts = self._knowledge_on_disk({str(row["path"]): str(row["sha256"]) for row in rows})
        return [
            self._knowledge_wire_row(row, verdicts.get(str(row["path"]), _UNKNOWN))
            for row in rows
        ]

    def _knowledge_wire_row(self, row: dict[str, Any], on_disk: str) -> dict:
        """One document as the frontend parses it.

        ``sha256`` IS DELIBERATELY ABSENT. The frontend has no use for it — the one
        question it answers is ``onDisk``, which is computed here — and a digest on
        the wire is a fact about a person's file travelling further than it needs to.

        ``detail`` and ``indexedAt`` are sent as ``null`` rather than omitted, unlike
        ``mcp.list``'s optional fields, because both are load-bearing on a row that
        HAS them: the panel prints ``detail`` verbatim as its one status line for a
        failed document, so a reader that had to tell "absent" from "empty" would be
        deciding what to show from the shape of the payload instead of from the row."""
        return {
            "id": row["id"],
            "displayName": row["display_name"],
            "path": row["path"],
            "status": row["status"],
            "detail": row["detail"],
            "chunkCount": row["chunk_count"],
            "flaggedChunks": row["flagged_chunks"],
            "byteSize": row["byte_size"],
            "addedAt": row["added_at"],
            "indexedAt": row["indexed_at"],
            "onDisk": on_disk,
        }

    def _knowledge_on_disk(self, expected: dict[str, str]) -> dict[str, str]:
        """``{path: "same"|"changed"|"missing"}`` for the paths the shell could judge.

        A path this cannot answer for is simply ABSENT from the result, and the caller
        reads that as ``"unknown"``. Everything that can go wrong lands there: no
        shell (the CLI, the fixture server, every test that wires no bridge), a shell
        that refused the batch, a digest the shell would not give (too big,
        unreadable, inside Addison's own data directory), or a malformed answer.

        NOTHING HERE RAISES. The list is what a person opens the panel to see, and a
        file they moved to another disk must not be able to empty the screen."""
        if not expected or self._shell_bridge is None:
            return {}
        try:
            answer = self._shell_bridge.digest_workspace_files(list(expected))
        except Exception:
            return {}
        digests = answer.get("digests") if isinstance(answer, dict) else None
        if not isinstance(digests, dict):
            return {}
        verdicts: dict[str, str] = {}
        for path, indexed_digest in expected.items():
            entry = digests.get(path)
            if not isinstance(entry, dict):
                continue
            if entry.get("missing"):
                verdicts[path] = _MISSING
                continue
            found = entry.get("sha256")
            if not isinstance(found, str) or not found:
                # The shell says it cannot tell. Neither can Addison, and saying
                # "unchanged" here would be the one wrong answer of the four.
                continue
            verdicts[path] = _SAME if found == indexed_digest else _CHANGED
        return verdicts

    # --- the picker + the indexing run (OFF the worker; store-free) --------
    def _knowledge_pick_and_prepare(
        self, doc_id: str | None, suggested_path: str | None
    ) -> tuple[dict | None, dict | None]:
        """Ask for a file, read it, chunk/screen/embed it. Returns ``(answer, job)``
        with exactly one of the two filled in: an answer to send back as it stands, or
        the parameters of a ``knowledge_commit`` job for the worker to write.

        **STORE-FREE, AND THAT IS THE CONTRACT OF THIS METHOD**, not an accident of
        how it happens to be written today. It runs on the ``knowledge-add`` thread so
        that a modal dialog somebody leaves open, and a local embedding run that takes
        a minute, block neither the read loop nor the worker. A ``sqlite3`` connection
        is usable only on the thread that opened it, so a single ``self.store`` here
        would be a cross-thread database access — which is why every decision that
        needs a row (is this a duplicate? is this still the same document?) is made in
        ``_knowledge_commit`` instead. A structural test holds the line.

        ``doc_id`` is None for an add and the row's id for a re-read.
        """
        if self._shell_bridge is None:
            # No desktop shell means no picker, which is indistinguishable from a
            # person closing one: nothing was chosen. Answered as a cancellation so
            # the panel shows nothing at all rather than an error about plumbing.
            return {"ok": False, "cancelled": True}, None
        embedder = self._embedder_ref() if self._embedder_ref is not None else None
        if embedder is None:
            return {"ok": False, "error": _CANNOT_ADD_DOCUMENTS}, None

        try:
            picked = self._shell_bridge.pick_knowledge_document(suggested_path)
        except RuntimeError as exc:
            # MATCHED ON THE SENTENCE, which is why it is a shared constant: a
            # cancelled picker and a refused file arrive through the same channel,
            # and they are not the same event. Cancelling shows nothing; a refusal
            # shows the shell's own words, untouched — it is the process that looked
            # at the file, and it wrote the sentence for the person.
            if str(exc) == PICKER_CANCELLED:
                return {"ok": False, "cancelled": True}, None
            return {"ok": False, "error": str(exc)}, None

        path = str(picked.get("path") or "")
        if not path:
            return {"ok": False, "cancelled": True}, None
        job: dict[str, Any] = {
            "docId": doc_id or uuid4().hex,
            "reindex": doc_id is not None,
            "path": path,
            "displayName": str(picked.get("displayName") or path),
            "sha256": str(picked.get("sha256") or ""),
            "byteSize": int(picked.get("byteSize") or 0),
            "model": getattr(embedder, "model", ""),
        }

        try:
            summary, rows = embedder.prepare(job["docId"], str(picked.get("content") or ""))
        except EmbeddingUnavailable as unavailable:
            # TWO FAILURES ARRIVE AS ONE TYPE, and they get opposite treatment. A
            # document with no text in it must leave NO row — there is nothing to
            # retrieve and nothing a Try again would fix. No local embedding model is
            # the other way round: the document is remembered, marked failed, and
            # carries the sentence, so pressing Try again once Ollama is running is
            # all that is left to do. They are told apart by the sentence the raiser
            # chose, because ``knowledge/index.py`` owns that vocabulary and a second
            # copy of the emptiness rule here would be a second thing to keep true.
            if str(unavailable) == NOTHING_TO_INDEX:
                return {"ok": False, "error": NOTHING_TO_INDEX}, None
            job["failure"] = str(unavailable)
            return None, job
        job["rows"] = rows
        job["dim"] = summary.dim
        return None, job

    # --- the write (ON the worker) ----------------------------------------
    def _knowledge_commit(self, params: dict) -> dict:
        """The ``knowledge_commit`` worker job: write what the thread prepared.

        THE ROW IS RE-READ HERE, and every check that needs one lives here rather than
        on the thread that did the reading. A modal picker can stand open for minutes:
        between the click that opened it and this job, the document can have been
        removed, or the same file added from another surface. Deciding from a row read
        before the dialog opened would be deciding from a fact that had already
        expired."""
        self._ensure_built()
        doc_id = str(params["docId"])
        path = str(params["path"])
        now = int(time.time())

        if params.get("reindex"):
            row = self.store.get_knowledge_document(doc_id)
            if row is None:
                return {"ok": False, "error": _NOT_IN_LIST}
            if str(row["path"]) != path:
                # A DIFFERENT FILE, refused rather than absorbed. `path` is UNIQUE, so
                # writing this one would either collide with another row or quietly
                # re-point a document at a file the person never added under that
                # name. Nothing is written: the row is exactly as it was.
                return {"ok": False, "error": _DIFFERENT_FILE}
        else:
            if self.store.knowledge_document_at_path(path) is not None:
                return {"ok": False, "error": _ALREADY_ADDED}
            self.store.add_knowledge_document(
                doc_id=doc_id,
                path=path,
                display_name=str(params["displayName"]),
                sha256=str(params["sha256"]),
                byte_size=int(params["byteSize"]),
                added_at=now,
            )

        failure = params.get("failure")
        if failure is not None:
            self.store.fail_knowledge_document(doc_id=doc_id, detail=str(failure))
        else:
            self.store.index_document(
                doc_id=doc_id,
                sha256=str(params["sha256"]),
                byte_size=int(params["byteSize"]),
                rows=list(params["rows"]),
                model=str(params["model"]),
                dim=int(params["dim"]),
                indexed_at=now,
            )

        row = self.store.get_knowledge_document(doc_id)
        if row is None:
            return {"ok": False, "error": _NOT_IN_LIST}
        # `ok: true` even for a document that failed to index. The write SUCCEEDED —
        # the document is remembered and the row says plainly what went wrong — and an
        # `ok:false` here would tell the panel to show an error line instead of the
        # row with its own Try again beside it.
        return {"ok": True, "document": self._knowledge_wire_rows([row])[0]}

    # --- re-read: the store half, which runs first ------------------------
    def _knowledge_reindex_target(self, params: dict) -> tuple[dict | None, str, str]:
        """``(refusal, doc_id, path)`` for ``knowledge.reindex`` — a store read, so it
        runs on the worker before the picker thread starts. The path it returns is
        where the dialog opens and what it pre-fills; it is a suggestion, never a
        permission, and ``_knowledge_commit`` checks what actually came back."""
        self._ensure_built()
        doc_id = params.get("id")
        if not isinstance(doc_id, str) or not doc_id:
            return {"ok": False, "error": _NOT_IN_LIST}, "", ""
        row = self.store.get_knowledge_document(doc_id)
        if row is None:
            return {"ok": False, "error": _NOT_IN_LIST}, "", ""
        return None, doc_id, str(row["path"])

    # --- removal ----------------------------------------------------------
    def _knowledge_remove(self, params: dict) -> dict:
        """knowledge.remove {id} -> ``{ok:true}`` | ``{ok:false, error}``.

        The chunks and their vectors go with the row: the schema cascades and
        ``PRAGMA foreign_keys = ON`` is set in ``Store.__init__``, so this is one
        delete and not three.

        NO RESTORE POINT, deliberately. The knowledge tables are excluded from
        snapshots (owner decision 4, 2026-08-24), so a restore point taken here would
        not contain the document it is supposedly a way back from — and offering one
        would be worse than offering none. The removal is permanent, the panel says
        so, and the file on disk is untouched.

        An id that is not there answers a sentence rather than ``{ok:true}``, unlike
        ``mcp.remove``'s idempotent shrug: this one is reached by clicking a row that
        is on the screen, so "there is nothing to remove" means the screen is stale
        and the person should be told."""
        self._ensure_built()
        doc_id = params.get("id")
        if not isinstance(doc_id, str) or not doc_id:
            return {"ok": False, "error": _NOT_IN_LIST}
        if not self.store.remove_knowledge_document(doc_id):
            return {"ok": False, "error": _NOT_IN_LIST}
        return {"ok": True}
