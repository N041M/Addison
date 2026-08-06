# Roadmap

Where Addison actually is, as of 2026-08-06.

This file holds status only, and it is the **only** place that does. The reasoning
behind any of it lives in [CLAUDE.md](CLAUDE.md), [docs/SAFETY.md](docs/SAFETY.md)
and [docs/HANDOFF.md](docs/HANDOFF.md). If this file and one of those disagree about
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

Then five of the eight steps from the July 2026 scope change:

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

Two things are left — 7 and 8 — and they are independent enough to take in
any order. Step 5.5 headed this list until 2026-07-31 and step 6 until 2026-08-06;
both are finished. Their entries stay below, in place, because they are recent
enough that people still ask what they covered.

5.5. **Containment for the coding harness — DONE 2026-07-31.** Step 5 gave
   Developer mode a real shell. What did not come with it was a boundary underneath
   the permission card — for five days one approved command could delete Addison's
   own restore points. This added a macOS sandbox around commands, bounded by the
   same folders you have trusted; a short list of things that cannot be approved at
   all; stripping of anything that looks like a password or key out of command
   output before a model sees it; and a record of what ran. Plan:
   [docs/step-5.5-containment-plan.md](docs/step-5.5-containment-plan.md).

   **The sandbox and the short list are built; the restore guarantee holds again
   in Developer mode.** Commands no longer run inside Addison's own engine — they
   run in the desktop shell, inside a macOS sandbox that can only write to folders
   you have trusted, and never to Addison's restore points however the path is
   spelled. On top of that, a command naming those restore points, or `~/.ssh` /
   `~/.aws` / `~/.gnupg` / a `.env`, is refused outright rather than offered as
   something to approve — everywhere a command can start: chat, a routine step,
   and a widget's Run button.

   **Also done: passwords and keys are stripped from command output before a model
   sees them, and every tool decision is recorded — including the ones Addison
   refused.** That record is what the MCP client (7) was waiting on, since the
   promise there is that outside tools are "gated, logged, undo-aware". On a
   machine that is not a Mac a command still runs unprotected; Addison says so in
   its answer rather than pretending otherwise, and the threat model now lists
   every boundary it does not defend (design-doc §9.x).

6. **Widget capability tiers — DONE 2026-08-06.** *Both halves are built; the
   tier lattice was cut.* **Half B** shipped 2026-08-06: a routine or widget made
   with developer abilities is listed in Simple as a disabled row that says why,
   instead of vanishing. **Half A** shipped the same day: three interactive kinds
   the Simple profile can use — a checklist you tick off, a note you edit, and a
   timer you start and pause yourself — with what you have done with one kept apart
   from what the widget IS, and left alone by a restore. What Addison tells the
   model it can build was rewritten to match. What was **deliberately not built**
   is the capability declaration the scope amendment sketched: a widget does not
   describe its own powers, because the list of kinds is closed and hard-coded,
   which is the same gate with nothing to get out of step. Code-backed widgets
   (monitors, scripts) are still Developer-only and still future work.
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
- **Running a command in a virtual machine first to see what it would do.** The
  appeal is obvious and the reasoning against it is not, so it is written down
  here rather than rediscovered. To find out what a command does you have to run
  it — so a command with side effects runs **twice**, and Addison deliberately
  allows a sandboxed command to reach the network, which means a "test" of
  `curl -X POST .../delete-account` deletes the account and then you approve it
  and it happens again. The commands most worth previewing are exactly the ones
  that cannot be previewed. Take the network away and ordinary work (`git fetch`,
  `npm install`) fails in the test but not for real, so the test says nothing.
  What Addison does instead is stronger for the same threat: the sandbox stops
  the change reaching the recovery floor, and a snapshot reverses whatever did
  happen. Confining and reversing beat predicting, because neither has to be
  right about the future.
  **Two narrower forms are NOT rejected** and are open questions in
  [docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md): showing what a delete would remove
  before you approve it, which needs no sandbox and no execution at all; and a
  copy-on-write clone for the file-only subset. Isolating *foreign code* is also
  a separate and live question — see the MCP transport decision in
  [docs/step-7-mcp-plan.md](docs/step-7-mcp-plan.md).

Addison also does not schedule itself, and that is not a gap. It is one of the four
guarantees in the [README](README.md).
