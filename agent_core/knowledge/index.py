"""The knowledge index: chunking, vector encoding, and similarity search.

**PROVIDER-FREE, AND THAT IS ENFORCED.** ``agent_core/tools/`` may not import
``agent_core/providers/`` (spec §2), and a retrieval tool has to import this module,
so this module may not reach a provider either — directly or through anything it
imports. It holds no network call, no model name it resolves, and no way to make an
embedding: it works on vectors somebody else produced.

Stdlib only, for the same reason ``policy.py`` is: the pieces here are pure functions
over text and numbers, and every one of them should be testable without a store, a
provider or a filesystem.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

# --- the words this feature uses when it cannot do something ---------------
#
# THEY LIVE IN THE PROVIDER-FREE HALF ON PURPOSE. `indexer.py` is what RAISES
# `EmbeddingUnavailable`, and `tools/search_knowledge.py` is what has to CATCH it and
# show the sentence — and a tool may not import the indexer (spec §2). Putting the
# exception type and its sentences here is what lets both sides name the same thing
# instead of keeping two spellings of one message in two modules that cannot see each
# other. Nothing here touches a provider; they are a class and three strings.


class EmbeddingUnavailable(RuntimeError):
    """No local embedding model answered. Carries the plain sentence to show."""


#: Said when there is no local embedding model (owner decision 3, 2026-08-24). NO
#: CLOUD FALLBACK, EVER: embedding a document in the cloud uploads its contents to a
#: provider, and Addison must not cross that line on somebody's behalf to save them
#: an inconvenience. Plain language, one suggested next step, no stack trace.
NO_LOCAL_MODEL = (
    "Addison couldn't do that, because the part that reads documents locally isn't "
    "available. Install Ollama and the '{model}' model, then try again."
)

#: Said when the file held nothing to index.
NOTHING_TO_INDEX = "There was no text in that document, so Addison didn't add it."

#: Said when a search runs before there is anywhere to search.
NOTHING_ADDED_YET = (
    "There are no documents to search yet. Add one in Settings, under Your documents."
)


#: Target size of a chunk, in characters. Not tokens: this module has no tokenizer
#: and inventing one would be a second, worse copy of the provider's. A thousand
#: characters is a long paragraph or two — big enough to carry an answer, small
#: enough that a retrieved passage is readable in a chat window.
TARGET_CHARS = 1000

#: How much of the previous chunk the next one repeats. Overlap exists because a
#: sentence that answers the question is often cut in half by a boundary that knew
#: nothing about it; without it, the answer is in the index and in no single chunk.
OVERLAP_CHARS = 150

#: Never emit a chunk shorter than this on its own. A trailing fragment is folded
#: back into the chunk before it instead, because a 12-character chunk embeds to
#: noise and then competes with real passages for a place in the results.
MIN_TAIL_CHARS = 200

#: Paragraph boundary: a blank line, however it is spelled.
_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Chunk:
    """One passage, and where in the document it came from.

    ``text`` is always exactly ``document[char_start:char_end]``. That is asserted
    rather than assumed (``test_offsets_are_real_slices``): the offsets are what let
    a retrieved passage say where it came from, and an offset that does not slice
    back to its own text is a citation to nowhere.
    """

    ordinal: int
    text: str
    char_start: int
    char_end: int


def chunk_text(
    document: str,
    *,
    target_chars: int = TARGET_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[Chunk]:
    """Split ``document`` into overlapping passages, preferring paragraph breaks.

    THE BOUNDARY IS CHOSEN, NOT COUNTED. Cutting every ``target_chars`` is simpler
    and splits mid-sentence roughly every time; this walks forward to the last
    paragraph break inside the window and cuts there, falling back to the last
    sentence end, and only then to the hard limit. A document with no breaks at all
    (minified JSON, a wall of text) still chunks, at the limit, which is the honest
    answer for a document that offers nothing to cut on.

    Empty or whitespace-only input yields no chunks, and that is not an error: a file
    can be empty, and the caller records a document with zero chunks rather than a
    failure nobody can act on.
    """
    if not document.strip():
        return []
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    # Overlap at or past the target would never advance, so the walk below would
    # not terminate. Refused loudly rather than clamped: a caller asking for it has
    # misunderstood something, and silently doing something else hides that.
    if not 0 <= overlap_chars < target_chars:
        raise ValueError("overlap_chars must be non-negative and smaller than target_chars")

    chunks: list[Chunk] = []
    start = 0
    length = len(document)
    while start < length:
        end = min(start + target_chars, length)
        if end < length:
            end = _best_break(document, start, end)
        # A tail too small to stand on its own joins the chunk before it rather
        # than becoming a chunk of noise.
        if length - end < MIN_TAIL_CHARS:
            end = length
        text = document[start:end]
        if text.strip():
            chunks.append(Chunk(len(chunks), text, start, end))
        if end >= length:
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _best_break(document: str, start: int, limit: int) -> int:
    """The best place to cut ``document`` at or before ``limit``.

    Paragraph break, then sentence end, then ``limit`` itself. Only breaks in the
    LAST HALF of the window are considered: a paragraph break just after ``start``
    is a true boundary and a terrible cut, because it would emit a chunk a tenth of
    the size it asked for and multiply the number of chunks for the whole document.
    """
    window = document[start:limit]
    floor = len(window) // 2

    last_paragraph = None
    for match in _PARAGRAPH.finditer(window):
        if match.end() >= floor:
            last_paragraph = match.end()
    if last_paragraph is not None:
        return start + last_paragraph

    sentence = max(
        max(window.rfind(mark + " "), window.rfind(mark + "\n"))
        for mark in (".", "!", "?")
    )
    if sentence >= floor:
        # +2 so the cut lands AFTER the mark and its following space, which keeps
        # the punctuation with the sentence it ends rather than starting the next
        # chunk with an orphaned full stop.
        return start + sentence + 2
    return limit


# --- vectors ---------------------------------------------------------------
#
# Little-endian float32, packed with `struct`. Not JSON: a 768-float vector is
# ~9KB as JSON text and 3KB as float32, and there are thousands of them. Not
# pickle, ever — this is a blob read back out of a database file, and pickle turns
# a corrupt or tampered row into code execution.

_FLOAT = "<f"


def encode_vector(values: list[float]) -> bytes:
    """Pack ``values`` as little-endian float32."""
    return struct.pack(f"<{len(values)}f", *values)


def decode_vector(blob: bytes) -> list[float]:
    """Unpack what :func:`encode_vector` produced.

    A blob whose length is not a multiple of four is refused rather than truncated:
    it is a corrupt row, and half a vector silently compares against real ones.
    """
    if len(blob) % struct.calcsize(_FLOAT):
        raise ValueError("vector blob is not a whole number of float32 values")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity, or 0.0 where it is undefined.

    Zero for a zero-length vector and for a dimension mismatch, rather than an
    exception. A mismatch means two different embedding models are in the table at
    once, which the caller already filters by model — and if one ever slips through,
    ranking it last is a better failure than taking down the whole search.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


@dataclass(frozen=True)
class Scored:
    """A candidate chunk and how well it matched."""

    chunk_id: str
    document_id: str
    score: float


def rank(query: list[float], candidates: list[tuple[str, str, bytes]], limit: int) -> list[Scored]:
    """The ``limit`` best of ``candidates``, each ``(chunk_id, document_id, blob)``.

    A FULL SCAN, DELIBERATELY. A personal knowledge base is thousands of chunks; a
    scan of those is milliseconds, and the alternative is `sqlite-vec` — a native
    extension loaded into the one process that holds the recovery floor, bought
    against a scale nobody here has. The moment a real index is slow, that is the
    moment to argue for the dependency, with a measurement.

    A candidate whose blob will not decode is SKIPPED, not raised on: one corrupt
    row must not make the whole knowledge base unsearchable.
    """
    if limit <= 0:
        return []
    scored: list[Scored] = []
    for chunk_id, document_id, blob in candidates:
        try:
            vector = decode_vector(blob)
        except (ValueError, struct.error):
            continue
        scored.append(Scored(chunk_id, document_id, cosine(query, vector)))
    # Sorted by score, then by chunk id, so an exact tie is stable across runs
    # rather than dependent on the order SQLite happened to return rows in.
    scored.sort(key=lambda s: (-s.score, s.chunk_id))
    return scored[:limit]
