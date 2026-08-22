"""Messaging-channel transports — how a person's phone reaches Addison.

A FOURTH SIBLING under the module-boundary rule (engineering-spec §2,
[CLAUDE.md](../../CLAUDE.md)): ``agent_core/tools/``, ``agent_core/providers/``
and ``agent_core/routines/`` may not import from one another, and this package
imports NONE of the three. It knows ``httpx``, its own dataclasses and nothing
else about Addison — the same placement ``mcp_client.py`` took, for the same
stated reason: a thing that is eventually consumed by all three may not live
inside one of them. ``tests/test_module_boundaries.py`` asserts it.

What lives here is everything transport-specific and nothing else:

  * ``adapter.py`` — the contract (``ChannelAdapter``), the four value types, the
    three-word failure vocabulary, and the backoff a transport carries.
  * ``telegram.py`` — the first adapter, and the only file in the design that
    knows a vendor's API shape.

Nothing above this package knows the word Telegram; nothing in it knows what a
turn, a tool or a permission gate is. That split is what makes a second transport
a file rather than a project (docs/messaging-channel-plan.md §3.2–§3.3).
"""
