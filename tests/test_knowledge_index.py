"""The knowledge index: the fence, the chunker, and the vectors.

`docs/plans/knowledge-retrieval-plan.md` owns the design. What is asserted here is
phase 1's load-bearing half: that `knowledge/index.py` — the module a retrieval TOOL
will import — cannot reach a provider, and that the pure functions under it hold the
properties a citation depends on.
"""

from __future__ import annotations

import ast
import pathlib
import struct

import pytest

from agent_core.knowledge import index

_PACKAGE = pathlib.Path(index.__file__).resolve().parent
_AGENT_CORE = _PACKAGE.parent


def _imported_modules(path: pathlib.Path, package: str) -> set[str]:
    """Every module name ``path`` imports, from its AST rather than by running it.

    BOTH HALVES OF AN ``ImportFrom``, and that is not thoroughness for its own sake:
    the first version of this collected only ``node.module``, so
    ``from agent_core import orchestrator`` was recorded as an import of
    ``agent_core`` — a package whose ``__init__`` imports almost nothing — and the
    walk stopped there. Its own mutation test caught it: a transitive reach into
    ``providers`` through that spelling passed the fence. So each alias is also
    joined onto the module, and a relative import is resolved against ``package``
    rather than skipped.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0] if node.level > 1 else package
                module = f"{base}.{node.module}" if node.module else base
            elif node.module:
                module = node.module
            else:
                continue
            names.add(module)
            # `from X import y` may name a MODULE y, not only an attribute of X.
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


def test_the_index_cannot_reach_a_provider_even_indirectly():
    """THE FENCE, and the reason this feature is two modules instead of one.

    `agent_core/tools/` may not import `agent_core/providers/` (spec §2). A retrieval
    tool has to import `index.py`, so `index.py` may not reach a provider either —
    and "reach" means transitively, because an import fence that only checks the
    first hop is a fence with a door in it.

    Walked rather than asserted at the first level, for that reason: this follows
    every `agent_core.*` module `index.py` imports, and theirs, and fails naming the
    CHAIN. The chain is the useful half of the message — "index.py -> foo -> bar ->
    providers.x" tells the next person which link to cut.
    """
    seen: set[str] = set()
    # (module name, how we got here)
    frontier: list[tuple[str, list[str]]] = [("agent_core.knowledge.index", ["index.py"])]
    while frontier:
        module, chain = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        relative = module.removeprefix("agent_core.").replace(".", "/")
        candidates = [_AGENT_CORE / f"{relative}.py", _AGENT_CORE / relative / "__init__.py"]
        source = next((c for c in candidates if c.exists()), None)
        if source is None:
            continue
        for imported in _imported_modules(source, module.rsplit(".", 1)[0]):
            assert not imported.startswith("agent_core.providers"), (
                "knowledge/index.py reaches agent_core.providers, so a tool that "
                "imports it would too — which is the module boundary (spec §2) the "
                "two-module split exists to keep. Move the provider call into "
                "knowledge/indexer.py, which the orchestrator owns and no tool may "
                "import.\n  chain: " + " -> ".join([*chain, imported])
            )
            if imported.startswith("agent_core."):
                frontier.append((imported, [*chain, imported]))

    # NOT VACUOUS: the walk has to have actually visited something beyond the
    # starting module, or a typo in the frontier would make this pass in silence.
    assert len(seen) >= 1 and "agent_core.knowledge.index" in seen


def test_the_indexer_is_the_half_that_may_call_a_provider():
    """The other side of the same claim, so the split is asserted in both
    directions. If `indexer.py` ever stops importing a provider, the two-module
    split has become decoration and this test says so rather than passing quietly."""
    imports = _imported_modules(_PACKAGE / "indexer.py", "agent_core.knowledge")
    assert any(name.startswith("agent_core.providers") for name in imports), imports


# --- chunking --------------------------------------------------------------


def test_offsets_are_real_slices_of_the_document():
    """A chunk's offsets must slice back to its own text, for every shape of input.

    THIS IS THE PROPERTY A CITATION RESTS ON. `char_start`/`char_end` are what let a
    retrieved passage say where in the document it came from; an offset that does not
    slice back to its text is a citation to nowhere, and nothing downstream can
    detect that.
    """
    documents = [
        "Alpha sentence here. Beta sentence here! Gamma sentence here? " * 40,
        "para one. " * 40 + "\n\n" + "para two. " * 40 + "\n\n" + "para three. " * 40,
        "no boundaries at all " * 300,
        "x" * 5000,                      # nothing to cut on anywhere
        "short document",                # smaller than one chunk
        "line\nline\nline\n" * 200,      # newlines but no blank lines
    ]
    for document in documents:
        chunks = index.chunk_text(document)
        assert chunks, document[:20]
        for chunk in chunks:
            assert document[chunk.char_start:chunk.char_end] == chunk.text
        assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_every_character_of_the_document_is_inside_some_chunk():
    """No gap. Overlap is fine and expected; a HOLE means a passage that can never be
    retrieved, and it would be invisible — the search would simply never return the
    paragraph nobody indexed."""
    document = "Alpha. Beta. Gamma. " * 400
    covered = bytearray(len(document))
    for chunk in index.chunk_text(document):
        for i in range(chunk.char_start, chunk.char_end):
            covered[i] = 1
    missing = [i for i, seen in enumerate(covered) if not seen]
    assert not missing, f"{len(missing)} characters are in no chunk, first at {missing[:1]}"


def test_an_empty_document_is_no_chunks_and_not_an_error():
    """A file can be empty. The caller records a document with zero chunks rather
    than a failure nobody can act on."""
    assert index.chunk_text("") == []
    assert index.chunk_text("   \n\n  \t ") == []


def test_an_overlap_that_would_never_advance_is_refused():
    """Overlap at or past the target means the walk never moves forward. Refused
    loudly rather than clamped: a caller asking for it has misunderstood something,
    and quietly doing something else hides that."""
    with pytest.raises(ValueError):
        index.chunk_text("some text", target_chars=100, overlap_chars=100)
    with pytest.raises(ValueError):
        index.chunk_text("some text", target_chars=100, overlap_chars=500)
    with pytest.raises(ValueError):
        index.chunk_text("some text", target_chars=0)


def test_chunks_overlap_so_a_split_sentence_survives_in_one_of_them():
    """The reason overlap exists at all. Without it a sentence cut by a boundary is
    in the index and in no single chunk, so nothing can retrieve it whole."""
    document = "Alpha. Beta. Gamma. " * 400
    chunks = index.chunk_text(document)
    assert len(chunks) > 2, "this document must be long enough to have boundaries"
    for earlier, later in zip(chunks, chunks[1:]):
        assert later.char_start < earlier.char_end, "consecutive chunks must overlap"


# --- vectors ---------------------------------------------------------------


def test_a_vector_round_trips_through_the_blob_encoding():
    values = [0.5, -1.5, 0.0, 3.25]
    assert index.decode_vector(index.encode_vector(values)) == values


def test_a_corrupt_blob_is_refused_rather_than_truncated():
    """Half a vector silently compares against real ones, and ranks somewhere
    arbitrary. Refusing is the only answer that cannot be wrong quietly."""
    with pytest.raises(ValueError):
        index.decode_vector(b"\x00\x00\x00")


def test_cosine_is_zero_where_it_is_undefined_rather_than_raising():
    assert index.cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert index.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert index.cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    # A dimension mismatch means two embedding models are in the table at once.
    # Ranking it last beats taking down the whole search.
    assert index.cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0
    assert index.cosine([], []) == 0.0
    assert index.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_ranking_orders_by_similarity_and_skips_a_corrupt_row():
    """One unreadable row must not make the whole knowledge base unsearchable."""
    query = [1.0, 0.0]
    candidates = [
        ("far", "doc", index.encode_vector([0.0, 1.0])),
        ("near", "doc", index.encode_vector([0.9, 0.1])),
        ("corrupt", "doc", b"\x01\x02\x03"),
        ("exact", "doc", index.encode_vector([1.0, 0.0])),
    ]
    ranked = index.rank(query, candidates, limit=10)
    assert [s.chunk_id for s in ranked] == ["exact", "near", "far"]
    assert index.rank(query, candidates, limit=2) == ranked[:2]
    assert index.rank(query, candidates, limit=0) == []


def test_ranking_breaks_a_tie_the_same_way_every_run():
    """Two identical scores must not depend on the order SQLite returned rows in,
    or the same query answers differently on different days."""
    query = [1.0, 0.0]
    same = index.encode_vector([1.0, 0.0])
    forward = index.rank(query, [("b", "d", same), ("a", "d", same)], limit=2)
    backward = index.rank(query, [("a", "d", same), ("b", "d", same)], limit=2)
    assert [s.chunk_id for s in forward] == [s.chunk_id for s in backward] == ["a", "b"]


def test_the_encoding_is_float32_and_not_something_wider():
    """Pinned because the schema comment promises it and the blob size is the whole
    reason the vectors are not JSON."""
    assert len(index.encode_vector([1.0, 2.0, 3.0])) == 3 * struct.calcsize("<f")
