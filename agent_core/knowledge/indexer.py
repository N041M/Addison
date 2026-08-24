"""Turning a document into embedded chunks.

**THIS IS THE HALF THAT MAY CALL A PROVIDER, AND THEREFORE THE HALF A TOOL MAY NEVER
IMPORT** (spec §2). It is owned by the orchestrator, like the context-budget
machinery and for the same reason: it is not a capability the model can invoke, it is
work the app does around a turn. Nothing here is registered, and phase 1 registers no
tool at all — so in this phase no model can reach any of it.

WHAT IT DOES, IN ORDER, AND WHY THE ORDER MATTERS:

1. chunk the text (``index.chunk_text`` — provider-free);
2. **screen each chunk and keep the verdict** (owner decision 1, 2026-08-24);
3. embed each chunk locally;
4. write document, chunks and vectors in ONE transaction.

Screening happens HERE rather than at retrieval because a knowledge base is a
standing channel: the same bytes would otherwise be re-screened on every query
forever, and — the part that actually matters — screening at index time is the only
moment at which a person can be told *"this document contains writing shaped like an
instruction"* while they can still decide not to add it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

from agent_core.knowledge import index
from agent_core.knowledge.index import (
    NO_LOCAL_MODEL,
    NOTHING_TO_INDEX,
    EmbeddingUnavailable,
)
from agent_core.providers.ollama_provider import default_base_url
from agent_core.screening import screen

#: The local embedding model Addison asks for unless told otherwise. Small, fast,
#: and the one an Ollama user is most likely to already have.
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

#: How long one embedding request may take. Generous next to a local model's real
#: latency and short enough that a wedged Ollama cannot hold an indexing run open
#: indefinitely.
EMBED_TIMEOUT_SECONDS = 60.0

@dataclass(frozen=True)
class IndexedDocument:
    """What one indexing run produced, for the caller to store and report."""

    document_id: str
    chunk_count: int
    flagged_chunks: int
    model: str
    dim: int


class KnowledgeIndexer:
    """Chunk, screen, embed. Owns no storage: the caller writes what comes back.

    ``client`` is an injected ``httpx.Client`` in tests (wired to a
    ``MockTransport``), exactly as the Ollama provider takes one. Nothing here ever
    reaches a real network in the suite.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._base_url = (base_url or default_base_url()).rstrip("/")
        self._client = client

    @property
    def model(self) -> str:
        return self._model

    def prepare(self, document_id: str, text: str) -> tuple[IndexedDocument, list[dict]]:
        """Everything about ``text`` that the store needs, or raise.

        Returns the summary and one row per chunk, each carrying its text, its
        offsets, its screening verdict and its vector. Raising rather than returning
        a half-built result is deliberate: a document that is partly indexed answers
        questions from the part that made it, which is worse than not being there.
        """
        chunks = index.chunk_text(text)
        if not chunks:
            raise EmbeddingUnavailable(NOTHING_TO_INDEX)

        rows: list[dict] = []
        flagged = 0
        dim = 0
        for chunk in chunks:
            verdict = screen(chunk.text)
            if verdict.flagged:
                flagged += 1
            vector = self.embed(chunk.text)
            dim = len(vector)
            rows.append(
                {
                    "id": uuid.uuid4().hex,
                    "ordinal": chunk.ordinal,
                    "text": chunk.text,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "flagged": 1 if verdict.flagged else 0,
                    # KINDS, never the matched text. Quoting an injection into a
                    # database row reproduces the payload somewhere else, which is
                    # the rule `ScreeningResult` states and this is the second
                    # place it has to hold.
                    "screened_kinds": ",".join(verdict.kinds) or None,
                    "vector": index.encode_vector(vector),
                }
            )
        summary = IndexedDocument(
            document_id=document_id,
            chunk_count=len(rows),
            flagged_chunks=flagged,
            model=self._model,
            dim=dim,
        )
        return summary, rows

    def embed(self, text: str) -> list[float]:
        """One vector for ``text`` from the local model, or raise.

        TWO ENDPOINTS, AND THAT IS NOT INDECISION. Ollama replaced ``/api/embeddings``
        with ``/api/embed`` and both are live in versions people are running today;
        asking the current one first and falling back on a 404 is the difference
        between working on the machine somebody has and working on the machine the
        documentation describes. Any other failure — a connection refused, a model
        that is not pulled — is the honest "no local model" answer.
        """
        body = {"model": self._model, "input": text}
        try:
            payload = self._post("/api/embed", body)
            vectors = payload.get("embeddings")
            if isinstance(vectors, list) and vectors and isinstance(vectors[0], list):
                return [float(v) for v in vectors[0]]
            # A 200 with nothing usable in it is not a working model.
            raise EmbeddingUnavailable(NO_LOCAL_MODEL.format(model=self._model))
        except _NotFound:
            payload = self._post("/api/embeddings", {"model": self._model, "prompt": text})
            vector = payload.get("embedding")
            if isinstance(vector, list) and vector:
                return [float(v) for v in vector]
            raise EmbeddingUnavailable(NO_LOCAL_MODEL.format(model=self._model)) from None

    def _post(self, path: str, body: dict) -> dict:
        client = self._client if self._client is not None else httpx.Client(
            timeout=EMBED_TIMEOUT_SECONDS
        )
        try:
            response = client.post(
                f"{self._base_url}{path}", json=body, timeout=EMBED_TIMEOUT_SECONDS
            )
            if response.status_code == 404:
                raise _NotFound()
            if response.status_code >= 400:
                raise EmbeddingUnavailable(NO_LOCAL_MODEL.format(model=self._model))
            return response.json()
        except (httpx.HTTPError, ValueError):
            # NO `from exc`, and no interpolation of the exception's text. httpx
            # puts the URL in its message and an adapter that chains one has been
            # a way to leak a credential before (docs/HANDOFF.md). Nothing here
            # carries a secret today; the habit is what keeps that true.
            raise EmbeddingUnavailable(NO_LOCAL_MODEL.format(model=self._model)) from None
        finally:
            if self._client is None:
                client.close()


class _NotFound(Exception):
    """Internal: this endpoint is not on this Ollama. Never reaches a caller."""
