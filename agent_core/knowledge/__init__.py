"""Knowledge: retrieval over documents a person attached
(``docs/plans/knowledge-retrieval-plan.md``).

TWO MODULES, AND THE SPLIT IS THE DESIGN. ``index.py`` is PROVIDER-FREE — chunking,
the vector encoding, the similarity search — and is what a retrieval tool is allowed
to import. ``indexer.py`` turns a document into embedded chunks and therefore calls a
provider, which is exactly what ``agent_core/tools/`` may never reach (spec §2).

The fence is asserted, not asked for: ``tests/test_knowledge_index.py`` reads
``index.py``'s imports with ``ast`` and fails if ``providers`` appears anywhere in the
module's transitive reach. A comment saying "do not import providers here" is the
version of this that stops being true quietly.
"""
