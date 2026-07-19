"""JSON-RPC handler namespaces for the Agent Core stdio server (engineering-spec §7).

``agent_core/main.py`` is the composition root: it owns lifecycle, the read loop,
the dispatch table, shared state, and the narrowing store/orchestrator/undo/routine
properties. The handler *bodies* live here, one module per §7 Method namespace, each
a mixin ``JsonRpcServer`` composes (see ``base.ServerContext`` for how the mixins see
the shared state the composition root supplies).

This package is composition-root code: like ``main.py`` it may import across the
tools/providers/routines subsystems. It must NOT be imported BY those subsystems.
"""
