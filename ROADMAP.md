# Roadmap

Where Addison actually is, as of 2026-07-26.

This file holds status only. The reasoning behind any of it lives in
[CLAUDE.md](CLAUDE.md), the
[scope amendment](docs/addison-scope-amendment-2026-07.md) and
[docs/HANDOFF.md](docs/HANDOFF.md). If this file and one of those disagree about
what a thing *is*, they win. If they disagree about whether it is *built*, check the
tree, because the prose here has been wrong before.

## Built

The v1 build order is finished and merged. That covers the schema and dataclasses,
the tool registry with its undo check, the permission gate, the Anthropic provider
with a model router and the orchestration loop, the rest of the tools with their
undo methods, the undo manager, the Tauri shell and its IPC, routines, the Setup
Assistant relay, Ollama with the full router, and profiles.

Several things landed alongside it: multiple providers in one picker (Anthropic,
OpenAI, Google, and any OpenAI-compatible server), the widget rail with its token
and latency data, conversation history, skills, Markdown and Mermaid rendering, and
the mode-scoped safety model where the profile decides the policy mode.

Then five of the eight steps from the July 2026 scope amendment:

1. **Snapshot and restore.** Restore points, saved automatically before risky
   changes and on request, written to the database and to plain files beside it so a
   restore survives a broken database.
2. **The Custom profile.** Two prompting guards you can tune, and an undeletable
   restore point minted whenever you weaken one.
3. **Routing strategies.** Quality first, cost first, local only, and a custom chain
   on Developer. Companion users see one toggle. Balanced was cut.
4. **Free model endpoints.** Add a server by asking for it, or ask Addison to run
   more cheaply, both previewed before they apply and both undoable.
5. **The coding harness.** Typed file tools bounded to folders you have trusted,
   with a workspace trust table behind them.

Also built, outside that sequence: the thread renders as a window of about thirty
messages instead of the whole conversation, which is what makes a long chat open
quickly.

## Next

Three steps are left in that sequence. They are independent enough to take in any
order.

6. **Widget capability tiers.** Widgets can already be built in every mode. This
   gives the safe vocabulary more to work with (checklists, notes, timers) and makes
   the guidance the model reads aware of what each mode can actually do.
7. **MCP client.** Consume external tools through the registry and the permission
   gate that already exist. Addison is a client here, never a server.
8. **The automation keyword gate.** Let Addison write automation that the operating
   system runs, with a keyword you type yourself needed to arm it. Addison still
   never triggers itself.

After that comes Phase 3: packaging, signing, notarisation, the auto updater, going
back to a previous app binary, and Secure Enclave identity. `updater.rs` is a nine
line stub today. There is also an approved plan for a Developer review surface in
[docs/phase-3-review-surface-plan.md](docs/phase-3-review-surface-plan.md), which is
blocked on steps 6, 7 and 8 and has not been started.

## Deliberately not being built

Not because they are hard. They were looked at and put down on purpose.

- Automatic task classification for routing. The four named strategies ship, but
  choosing one per task by itself is a later problem.
- The context budget manager and automatic continuation of long conversations. The
  schema and the orchestrator machinery are there. The feature is not.
- Messaging channels, and a UI for editing the steps of a routine.
- Rewriting the agent core in Rust.
- Sharing routines by export and import, and screening untrusted content. Both
  become more interesting once MCP and free endpoints are in wide use.

Addison also does not schedule itself, and that is not a gap. It is one of the four
guarantees in the [README](README.md).
