# Routine sharing (export and import)

**Status: BUILT for v1 (2026-08-15), in the form this document describes and at the
strength it states.** Design-doc §11 item 5 is the origin and stays as history;
this file owns the subject from here on. Anything else that mentions sharing a
routine links here.

It shipped as four pull requests in one day: the portable format (#121), the narrow
taint card (#124), the wiring (#125), and the surface plus this document. What
follows is the record of what is built, the four owner decisions it rests on, and,
at the end and in more detail than the rest, what an imported routine can still do
that nothing here catches.

The one sentence to carry away, because every other claim in this file is a
qualification of it: **importing a routine grants it nothing.** A routine is a
plan, the plan is data, and every action in it goes through the same permission
gate the same way it would if the person had asked for it out loud. Addison has not
checked what the plan is for and does not claim to have.

## 1. What is built

**The portable format: `agent_core/routines/portable.py`.** A pure module, no
store, no registry, no gate, no RPC. `to_portable` builds the dict a routine
travels as and `parse_portable` reads one back, and the two do not share a code
path with `routine_to_json`, whose reader parses trusted database rows and believes
what it finds. The travelling format is a **whitelist**: version, name,
description, variables, and per step the step id, tool id, argument template,
dependencies, failure behaviour and model role. A field added to `Routine`
tomorrow does not travel until somebody writes its name in that list, which is the
property a blacklist cannot have. The id, the sender's mode stamp, their
conversation id, their run history and any pinned model id are left behind, each
with its reason recorded beside it.

The reader assumes the file is hostile: it never raises, it returns one plain
sentence instead of a traceback, and it bounds every walk (bytes, steps, variables,
nesting depth, string length) so it terminates on the worst input somebody can
author. It mints a fresh id, so an arriving routine can never take the place of a
row somebody already has. Two adds of the same file are two routines for the same
reason.

**The three RPC methods: `agent_core/rpc/routines.py`.** `routine.export`
serialises through `to_portable` and hands the JSON to the shell's ordinary
save-a-new-file dialog, so the person names the file themselves.
`routine.importPreview` opens the picker through the shell (the core never learns a
path), parses the bytes, checks that every step names an action this build actually
holds, asks the same needs-Developer question the library list asks, screens the
wording, and **saves nothing at all**. `routine.importConfirm` is the only call
that writes. The split is the safety property: everything that reads a stranger's
bytes happens in a call that cannot leave a row behind.

The confirm takes no parameters and trusts nothing the preview said. What is held
between the two calls is the file's own parsed bytes, and the confirm re-parses and
re-screens them, so a name, a step or a verdict edited in the webview on the way
back changes nothing. A restore point is taken first, and a restore point that
cannot be taken refuses the import (G3).

**Screening at the door.** The picked file's text is screened
(`agent_core/screening.py`), which makes import the fifth origin of screened text.
A flagged file is not refused: the stored description is marked with the caller's
verdict, so the model reading it later reads it as text, and the preview carries one
plain sentence saying so. See [untrusted-screening-plan.md](untrusted-screening-plan.md),
which owns screening.

**The taint card: `agent_core/routines/taint.py`.** When a routine step's resolved
arguments contain text that came out of an earlier file-reading step **in the same
run**, and the step about to run is network-bound, the permission card carries one
extra plain line naming the flow. The trigger is exact containment of the file's
output inside an argument string. Nothing fuzzy, nothing statistical. It is wired to
`force_card` on the gate (owner decision 4B), which is a tightening in one
direction only: it can turn no card into a card and can never turn a card into no
card, and it cannot reach the arming ceremony, which is the stronger card and stays
above it.

**The surface: `shell/src/components/RoutineLibrary.tsx` and
`RoutineImportCard.tsx`.** Share on each row, one row at the end for adding a
routine somebody sent, and between them a card carrying the name, the description,
the numbered steps in plain verbs, what the routine will ask for each time, the
needs-Developer notice when it applies, the screening note when there is one, and
the three sentences the core makes mandatory:

> This routine can't do anything you haven't approved. Addison still asks before
> each action, exactly as it does now.
>
> Addison hasn't checked what this routine is for. Only add it if you trust the
> person who sent it.
>
> You can delete it at any time, and Addison saves a restore point before adding it.

They ride on every preview, flagged or not, because the honest thing to say about a
file from somebody else is the same whether or not a pattern matcher recognised
anything in it. `shell/src/__tests__/routineSharing.test.tsx` asserts all three word
for word against the fixture the core itself generates, so softening one fails the
suite rather than shipping.

**One column: `routines.imported_at`.** Nullable, the epoch second the routine
arrived from a shared file. Provenance stated rather than inferred from a null
foreign key, and display only, on the same terms as `created_in_mode`: nothing
decides anything from it.

## 2. Owner decisions, 2026-08-15

Taken together on the day the work started, and recorded here in the owner's words
as decisions rather than as options.

1. **1A. Any profile may import, and a routine that needs Developer lands listed
   and switched off.** The alternative was making import a Developer capability.
   That would have been the wrong fence: reading a file and describing it is not a
   developer ability, and Simple is exactly the profile whose person is likeliest
   to be sent a routine by somebody who set one up for them. What a profile governs
   is what may RUN, and that machinery already exists and already says why on the
   row (owner decision 2026-08-06). Export is scoped the same way, in every
   profile, including on a row this profile cannot run: passing a plan on is not
   running it.
2. **2A. Import screens the picked file's text, which makes it the fifth screening
   origin.** Decision 5 of 2026-08-13 left local file reads unscreened because they
   are the person's own material, reached through the person's own consent. That
   reasoning is intact and this is a refinement of it, not a reversal: a routine
   file is picked by the person but **written by somebody else**, which is the
   condition the reasoning turned on, and the decision recorded the revisit
   condition in as many words. Screening it costs the false-positive budget almost
   nothing, because a routine file is short and its prose is a description.
3. **3A. A command step is refused in both directions.** Not exported, and a file
   carrying one is refused before anything else about it is read. A shell line
   somebody typed into their own Developer session is one object; the same shell
   line arriving as a one-click artifact from a stranger, to be run by pressing a
   button in a list, is a different object with a different risk, and the profile
   check that governs the first one was never asked about the second. Until this
   decision is revisited the format simply cannot express a command.
4. **4B. The narrow taint card, and the `force_card` tightening that makes it
   real.** The choice was between general taint tracking through the run and one
   exact rule. One exact rule, because the line is a prompt for a person reading a
   card and not a containment boundary, and a card that guesses wrong is a card
   people learn to click through. `force_card` came with it: the line is the
   control, so a step that auto-granted or rode a coarse remembered grant would
   have carried the line onto a card nobody was ever shown.

## 3. What stands in the way of what

Read this table as a list of what each row is worth, not as coverage. The right
column is the part that matters.

| The thing somebody sends you | What stands in its way | What that is worth |
|---|---|---|
| A step that runs a command | The format cannot express one, and a file carrying one is refused before anything else is read | Complete for this shape. It is a property of the format, not a check that can be talked past |
| A tool id naming somebody else's tool server | Refused on the way in | Complete for this shape |
| A default or argument pointing at a folder on the sender's machine | Export refuses and names the field | Only on the way OUT, and it is about the author sharing something they did not mean to |
| A file that is enormous, deeply nested, or a loop of steps waiting on each other | The reader's ceilings and its dependency check | Complete as a bound on cost. It says nothing about intent |
| A step naming an action this build does not have | Refused at preview | Complete for this shape |
| A plan whose steps do something destructive | The permission gate, per invocation, exactly as if you had asked out loud | This is the real control, and it is the one this feature rests on |
| A plan needing abilities the active profile does not have | Listed, switched off, and it says why | Complete, and it is the same machinery every waiting artifact uses |
| Wording in the file written as an instruction to Addison | Screening, one plain note, and the stored description is marked | A backstop. Writing in a shape nobody listed passes untouched |
| A step that puts an earlier step's file text on the wire in the same run | One extra line on that step's card, and the card is forced | Exact containment only, one run only. See below |
| A file pretending to be a routine you already have | A fresh id at parse, always | Complete for this shape |

## 4. What remains uncaught

None of this is softened, and none of it is a defect list. These are the things a
person could reasonably assume are handled, and are not.

**Injection phrased as ordinary prose.** Screening is six enumerated shapes. A
routine description written as plain, reasonable-sounding text that a model will
nonetheless act on is not flagged, is not marked, and reaches the model as ordinary
description. The mitigation is the one this document opened with: nothing the model
concludes from that text can act without a card.

**The sequence-exfiltration chain, beyond the one edge 4B catches.** The taint line
fires on a file-read output appearing verbatim in a network-bound step's arguments
within one run. Three shapes of the same attack are outside it, deliberately:

- **Laundered text.** A step between the read and the send that summarises,
  translates or re-words the file's contents produces a new string, and exact
  containment does not find the original inside it. No line appears.
- **A chain across two routines.** Taint is per run and dies with the run. One
  routine reads, another sends, and to this module they are two unrelated runs.
- **Contents the person pasted themselves.** Text put into a routine variable by
  hand never passed through a file-reading step, so nothing knows where it came
  from.

In all three the ordinary card still appears for the network step; what is missing
is the sentence saying where the text came from.

**A plan whose danger is entirely in the values.** The import preview shows the
steps in plain verbs and the questions the routine will ask. It does not and cannot
show the resolved arguments, because they do not exist until the routine runs and
the person answers. A plan that looks unremarkable at import and does something
unwelcome with a substituted value is not distinguishable at the card the person
sees when deciding to add it. **The run card is where those values appear**, in
full, per invocation, which is why the run card and not the import card is the
control this rests on.

[KNOWN-GAPS.md](KNOWN-GAPS.md) tracks all four as live items.

## 5. What is deliberately NOT built

- **No marketplace, no directory, no registry.** A file, sent the way people send
  files.
- **No trust or reputation of any kind.** No signing, no author identity, no
  "verified" anything. There is nothing to display and nothing to be fooled by.
- **No permission travelling with the routine.** The format cannot express a grant,
  because there is no grant to express: an imported routine carries zero
  permissions and asks like any first run (§6.4's no-escalation rule travels with
  it, unchanged).
- **No repair of a file that will not read.** The reader refuses and says which
  part, and never quietly fixes anything, in either direction. An author who
  learned on somebody else's machine that their routine had silently changed would
  be worse off than one told which field to edit.
- **No import of an MCP-backed step**, which names a server the receiver either
  does not have or, worse, has under the same name pointing somewhere else.
