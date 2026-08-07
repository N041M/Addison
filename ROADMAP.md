# Roadmap

Where Addison actually is, as of 2026-08-07.

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
quickly; and the two model pickers are a folder tree — company, then family, then
model, with one folder open at a time — which is what keeps them a menu now that a
single connected provider can contribute twenty-two models.

## Next

**One thing is left — step 8.** Step 7 finished what it is doing for v1 on
2026-08-07: four of its five phases have landed (saving a server's address,
checking what it offers, running one with your approval every time in the
Developer profile only, and passing back what it answered), and the fifth is a
pair of things v1 deliberately does not include. Step 5.5 headed this list until
2026-07-31 and step 6 until 2026-08-06; both are finished. Their entries stay
below, in place, because they are recent enough that people still ask what they
covered.

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
7. **MCP client — DONE for v1 2026-08-07. Phases 1, 2, 3 and 4 of five are built
   (2026-08-06, and three on 2026-08-07); the fifth is a later option, not a piece
   of v1 that is missing.** Consume external tools through the registry and the
   permission gate that already exist. Addison is a client here, never a server.
   The five phases and what each one covers are in
   [docs/step-7-mcp-plan.md](docs/step-7-mcp-plan.md).

   **What the fifth phase holds, and why it is not v1:** talking to servers that
   run as a program rather than at a web address, which needs containment nobody
   has built (see the transport note below), and letting a tool server's tools be
   used in the Simple profile, which is a decision the owner deferred rather than
   answered. Both are written down; neither is started; nothing built so far leans
   on either.

   **Transport is HTTP only for v1** (owner decision 2026-08-06): a saved server
   is a web address, never a program to launch, which is why the client can live
   in the agent core and needs nothing new in the desktop shell. Servers that talk
   over stdio need containment that has not been built, so they wait — the plan
   keeps both routes to them written down.

   **What phase 1 shipped:** a Developer-only Settings section where you can save,
   list and remove a tool server, and a restore point saved before each change so
   the list can be rolled back. It stored an address and nothing happened, which is
   exactly what that phase was for.

   **What phase 2 shipped:** a "Check now" button. Press it and Addison connects to
   that one server, asks what it offers, and lists it — on the Tools page, in a
   section of its own headed by the server it came from. Every entry was marked as
   something Addison could see and not yet use, because at that point that was the
   whole truth. A server that is switched off, or that wants a sign-in Addison
   cannot do, says so in one plain sentence on its own row rather than failing
   quietly. Nothing is checked unless you ask: Addison makes no connection you did
   not just cause, nothing is checked when the app starts, and what a check found is
   forgotten when the app closes — so after a restart a server honestly reads "not
   checked yet".

   **What phase 3 shipped:** those tools can now be used — and **every call is
   treated as one that cannot be undone**, which in the Developer profile means
   Addison asks you before each one. Approving a tool once never approves it again,
   because a tool server is somebody else's program and Addison is not in a position
   to know what any of it will do; the card says exactly that, and names the server
   the tool came from. (The Custom profile's "ask me less often" settings are the one
   thing that changes how often you see that card, and they change nothing about how
   a tool server's tool is treated — [docs/SAFETY.md](docs/SAFETY.md) owns those
   settings.) Anything a server sends back has the passwords and keys Addison
   recognises stripped out of it before a model sees it, is trimmed if it is
   enormous, and leaves a
   permanent record of what happened — whether you approved it, whether it ran, and
   whether anything was stripped. A server that goes quiet costs you a wait of a few
   seconds, never the rest of what you were doing, and one you have since removed is
   not called at the address it used to have. Nothing about the Simple profile
   changed at any point: a Simple-profile person sees none of this and can run none
   of it.

   **What phase 4 shipped:** the rest of what a tool sends back. A tool can answer
   with more than words — pictures, sound, files, and a machine-readable version of
   the same answer — and Addison now says what it got rather than quietly keeping
   the words and dropping the rest. It passes on the words (including words that
   arrived wrapped in a file) and the machine-readable part, both through the same
   stripping first; it does not pass on pictures, sound or files, because
   they come from a program nobody here has checked and nothing on this side needs
   them. What it left out it says in a line of plain English — *"the tool also
   returned 2 images and 1 file"* — instead of leaving you to wonder. A long answer
   is trimmed to a size that fits, once for the whole answer rather than once per
   piece, and the trim now says how long the answer was and how much of it you are
   seeing.
8. **The automation keyword gate.** Let Addison write automation that the operating
   system runs, with a keyword you type yourself needed to arm it. Addison still
   never triggers itself.

After that comes Phase 3: packaging, signing, notarisation, the auto updater, going
back to a previous app binary, and Secure Enclave identity. `updater.rs` is a nine
line stub today. There is also an approved plan for a Developer review surface in
[docs/phase-3-review-surface-plan.md](docs/phase-3-review-surface-plan.md), which is
blocked on step 8 alone now that 6 and 7 have landed, and has not been started.

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
  a separate and live question — it is what a stdio MCP server would need, and it
  is why v1 talks to tool servers over the web instead
  ([docs/step-7-mcp-plan.md](docs/step-7-mcp-plan.md) owns that decision).

Addison also does not schedule itself, and that is not a gap. It is one of the four
guarantees in the [README](README.md).
