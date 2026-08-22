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

**Untrusted-content screening, built 2026-08-13.** Text a tool brings back from
outside (a web page, a command's output, an answer or a tool description from a
tool server) is checked for writing shaped like an instruction to an assistant, and
anything flagged reaches the model with a plain note in front of it saying to treat
it as information. The person sees one sentence when it happens, and the audit row
records which kinds were recognised. It is a pattern layer and a backstop: writing
in a shape nobody listed passes untouched, and the permission gate is still the
only thing that decides what may run.
[docs/untrusted-screening-plan.md](docs/untrusted-screening-plan.md) owns it.

**The context budget manager, built 2026-08-14.** A chat that gets close to what
the answering model can hold is condensed at the end of a turn: Addison summarises
the older part, starts a continuation carrying that summary, the facts you have
confirmed and the last four turns word for word, and says one plain sentence. The
lineage and the summary are stored, and nothing is deleted: the whole original
transcript stays where it was. If anything about it cannot be done properly, from a
model that will not say how much it holds to a summary that comes back empty, the
chat is left exactly as it was and you are told nothing. The relay a person uses
before they have a key of their own is refused outright. A chat that outgrows its
model anyway now gets a plain sentence saying so, which is a thing the spec had
claimed for months and no code had ever produced.
Since 2026-08-22 a continued chat also
says so where you can see it later: a marker at the top of the thread, drawn from
the stored row rather than from the note, and one entry in the history list with
the earlier part under it. **One limit is still real**: the summary call's tokens
are not in the cost views, so a long chat costs a little more than they say.
[docs/context-budget-plan.md](docs/context-budget-plan.md) owns it, and
[docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md) tracks that limit.

**Sharing a routine, built 2026-08-15.** A routine can be saved as a file and sent
to anyone else using Addison, and a file somebody sends you can be added from the
routine list in any profile. Before anything is added you see what the routine says
it does, step by step, what it will ask you for each time, and three sentences that
do not change: it can do nothing you haven't approved, Addison hasn't checked what
it is for, and you can delete it, with a restore point already saved. **Adding a
routine grants it nothing** and it asks like any first run. A routine that runs a
command on your computer cannot be shared or added at all, in either direction; one
that needs the Developer profile arrives listed and switched off, saying so; and
wording in the file that reads like an instruction to Addison gets the same plain
note any web page would. One extra line appears on a card when a step would put text
from a file the same run just read onto the web.
**Its limits are real**: that line is exact matching inside one run, so text that was
reworded on the way, a chain across two routines, and contents somebody pasted in by
hand produce no line; wording shaped as ordinary prose is not flagged; and a plan
whose danger is entirely in the values it substitutes looks unremarkable at the
moment you add it. The run card, which shows those values, is the control.
[docs/routine-sharing-plan.md](docs/routine-sharing-plan.md) owns it, and
[docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md) tracks what remains uncaught.

## Next

**Nothing from this sequence is left. Step 8 finished on 2026-08-08, and with it
the whole July-2026 scope change.** What comes next is Phase 3, and Phase 3 is two
tracks rather than one. The first is the packaging track it has always been —
packaging, signing, notarisation, the auto updater, going back to a previous app
binary, and Secure Enclave identity; `updater.rs` is still a nine-line stub and is
the only `TODO(step N)` marker in the tree. The second was added to the phase on
2026-07-25: the **Developer review surface** — a file tree over the trusted folders,
a read-only viewer, a diff of every edit Addison has made that is still on disk, and
a way to put one file back. **All five of the plan's Build sections landed 2026-08-08**
— the read paths, the diff, per-file revert, the screen itself and its Monaco skin — so
the surface is COMPLETE PENDING ITS MANUAL PASS: what is left is
[docs/TESTING-CHECKLIST.md](docs/TESTING-CHECKLIST.md) §13c, which is the only place the
widened content-security policy is enforced by a real webview, on all three platforms.
See the note further down and
[docs/phase-3-review-surface-plan.md](docs/phase-3-review-surface-plan.md).

Step 5.5 headed this list until 2026-07-31, step 6 until 2026-08-06, step 7 until
2026-08-07 and step 8 until the following morning; all are finished. Their entries
stay below, in place, because they are recent enough that people still ask what
they covered.

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
8. **The automation keyword gate — DONE: phases 1–3 on 2026-08-07, phase 4 on 2026-08-08.** Let Addison write automation that the operating
   system runs, with a keyword you type yourself needed to arm it. Addison still
   never triggers itself. The keyword is a code Addison shows you and you retype,
   minted fresh for each arming — decided 2026-08-07, and what made a fixed prefix
   the wrong answer is that anything able to write English could tell you to type
   one. The four phases and the decisions around them are in
   [docs/step-8-automation-plan.md](docs/step-8-automation-plan.md).

   **What phase 1 shipped (2026-08-07):** the fence and the table, with the gate
   and everything that can arm still to come. Before this, a folder like the one
   where the Mac keeps its login-time jobs could be trusted like any project
   folder, and a file written there would have been a job armed with no keyword —
   found and closed the same day. Addison now refuses to trust those folders, to
   run commands that reach into them, and to run the four programs whose job is
   handing work to the OS clock — and the refusal says what is actually going on
   rather than borrowing the wrong sentence. The table that will hold automations
   exists and is covered by restore points, deliberately with no way to add a row
   and no memory of "armed": what is armed is the OS's truth, so a restore can
   never re-arm anything by putting a database row back.

   **What phase 4 shipped (2026-08-07, same day) — the last of it.** Two kinds of
   honesty. Whether an automation is actually running is asked of the computer
   when you open the page, never remembered — so after restoring a backup, or
   reinstalling, or deleting the file by hand, what you see is what is true rather
   than what Addison last wrote down. And automations no longer vanish when you
   switch to the Simple profile: they are listed, plainly marked as waiting, and
   still removable — because a switch that hides your own work reads like Addison
   deleted it, and because turning something off should never be the thing a
   profile change traps you out of. Simple does not show the command or offer to
   arm anything; it shows what you wrote and whether your computer is running it.

   **What phase 3 shipped (2026-08-07, same day) — the part the step is named
   for.** You can now switch an automation on, and doing it takes more than a
   click: Addison shows you exactly what will run, where it will be saved, and two
   plain sentences about what you are agreeing to — that it runs on its own
   schedule even when Addison is closed, and that it runs outside Addison's
   sandbox — and then shows a short code you type back to confirm. The code is
   different every time and Addison itself cannot supply it, which is what stops a
   web page or a message from talking you through arming something. Three wrong
   tries and it stops and asks you to start over. Switching one back off is an
   ordinary confirmation with no code, because turning something off should never
   be the hard part. **Addison still never triggers itself**: it writes a file the
   operating system reads, the OS does the running, and arming deliberately does
   not cause a first run. Off a Mac it says so plainly and does nothing.

   **What phase 2 shipped (2026-08-07, same day):** asking Addison for an
   automation now writes one down — in the Developer profile only. Addison saves
   what to run and when, shows the schedule as one plain sentence ("Every Monday
   at 7:30"), and shows in chat the exact text it would one day hand to the
   computer, before anything can act on it. It refuses at the door what it would
   refuse to run — a command reaching into protected folders, a command that
   itself hands work to the OS clock, anything that looks like it has a password
   or key in it (saved automations are copied into restore points, so a secret
   belongs in a file the command reads instead). Drafts are listed in Settings
   with a remove button, every answer ends by saying nothing is armed, and
   turning one on is still impossible anywhere in the app — that is the next
   phase, behind the retyped code.

After that comes Phase 3: packaging, signing, notarisation, the auto updater, going
back to a previous app binary, and Secure Enclave identity. `updater.rs` is a nine
line stub today. **Phase 3 also carries a second track**, added to it on 2026-07-25 —
the Developer review surface in
[docs/phase-3-review-surface-plan.md](docs/phase-3-review-surface-plan.md). It waited
on steps 6, 7 and 8, and all three landed on 2026-08-06 and 2026-08-07; the three
fixes the plan asked for first closed on 2026-08-08, each in its own change. **Built,
start to finish, the same day**: Build §1 (the read paths — `workspace.listDirectory` /
`readFile` as RPC, one resolution per call), §2 and §3 (the list of every edit Addison
has made that is still on disk, the before-and-after for one of them, and putting one
file back to the state Addison found it in), and §§4–5 — the screen itself, a third
sidebar entry that exists only under the Developer and Custom profiles, with Monaco
skinned from the palette the app already had. Shipping the editor meant widening the
webview's content-security policy by two directives (`style-src 'unsafe-inline'`, which
Monaco cannot run without, and `img-src 'self' data:`, which the previous
`default-src 'self'` refused) while tightening four others; the policy is pinned by
`tests/test_csp_is_pinned.py` and the one path by which markup the app did not author
reaches the page has its CSS stripped before injection. **What remains is not code:**
the §13c manual pass in [docs/TESTING-CHECKLIST.md](docs/TESTING-CHECKLIST.md), which is
where a real webview says what that policy actually refuses, plus the plan's follow-up
list, which is down to one item: an editor zoom control, which the 12px type size does
not settle. JSON highlighting and the post-restart revert case were built on
2026-08-13.

## Deliberately not being built

Not because they are hard. They were looked at and put down on purpose.

- Automatic task classification for routing. The four named strategies ship, but
  choosing one per task by itself is a later problem.
- Messaging channels, and a UI for editing the steps of a routine.
- Rewriting the agent core in Rust.
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
  **Two narrower forms are NOT rejected.** The first is now BUILT (5.6,
  2026-08-13): when a command would delete something, the card also says how much
  that is ("About to delete 1,240 files in 12 folders"), counted by looking at
  the folder, with no sandbox and nothing run. The second, a copy-on-write clone
  for the file-only subset, is still an open question in
  [docs/KNOWN-GAPS.md](docs/KNOWN-GAPS.md), which owns both.
  Isolating *foreign code* is also
  a separate and live question — it is what a stdio MCP server would need, and it
  is why v1 talks to tool servers over the web instead
  ([docs/step-7-mcp-plan.md](docs/step-7-mcp-plan.md) owns that decision).

Addison also does not schedule itself, and that is not a gap. It is one of the four
guarantees in the [README](README.md).
