# Untrusted-content screening

**Status: BUILT for v1 (2026-08-13), in the form this document describes and at the
strength it states.** Design-doc §11 item 6 is the origin; this file owns the
subject from here on. Anything else that mentions screening links here.

This document is the record of what shipped, what it is worth, and what it is not.
It is deliberately short on ambition, because the thing itself is deliberately
modest: a pattern layer that puts a plain note in front of text that reads like an
instruction to an assistant. It is not a floor, not a guard, and not an invariant,
so [SAFETY.md](SAFETY.md) says nothing about it beyond what the permission gate
already promised.

## 1. What is built

**The module: `agent_core/screening.py`.** Stdlib `re` and dataclasses, no I/O, no
imports from `tools/`, `providers/` or `routines/`, so anything may import it
without touching the module-boundary rule. Two functions and one constant:

- `screen(text)` returns a frozen `ScreeningResult` carrying `kinds` and
  `flagged`. It never alters, truncates or returns the text.
- `mark_untrusted(text, verdict=None)` returns the text with `UNTRUSTED_MARKER` in
  front of it when it was flagged, and the same object back when it was not.
- Six rules, each anchored on a verb AND its object, a line start, or a literal
  string: instruction override, identity reassignment, authority header, role or
  turn marker, instruction disclosure request, and an impersonation of Addison's
  own untrusted-content note. Every gap between anchors is a bounded, newline-free
  character class, so no rule can backtrack over a hostile page's chosen length.

Two properties the module states about itself and the code holds by construction:
detection never drops or rewrites content (removing the passage would destroy the
evidence and leave the model answering from a hole), and the marker is idempotent
(it matches no rule of its own, and the function refuses to wrap text that already
opens with it).

**Kinds only, never the matched text and never a length.** Quoting an injection
into a result, a log or an audit row would reproduce the payload in a second place.
This is the same rule `agent_core/redaction.py` keeps.

**The five origins.** Screening in the orchestrator runs on tool results carrying
`content_origin == "external"`, which today is exactly four tools: `web_search`,
`read_web_page`, `run_command`, and any tool discovered from an MCP server
(`mcp_catalog`). **The fifth is not a tool result** and was added on 2026-08-15: the
text of a routine file somebody else wrote, screened at
`routine.importPreview` and again at `routine.importConfirm`
(`agent_core/rpc/routines.py`; [routine-sharing-plan.md](routine-sharing-plan.md)
owns that feature). Addison's own sentences (a refusal, a calculator answer, a steer
after a denied step) are not screened, for the same reason the redactor's own
markers are not: marking Addison's words as untrusted teaches the model to discount
the mark.

**The orchestrator seam** (`_run_tool_calls` in `agent_core/orchestrator.py`), and
it is the one place a tool result is screened. The order is the order the code
reads in: a tool cleans and trims its own output where it already does that, then
this screens what actually came back, then the redact-classify pass describes the
same bytes for the audit row. When something is flagged, the model's copy of the
result is the marked text, and the person hears one plain sentence on the Activity
Panel channel the routing and free-model notes already use. That sentence names no
rule and quotes nothing.

**MCP ingest** (`_screen_offer` in `agent_core/mcp_client.py`). A server's tool
DESCRIPTION and the strings of its bounded `inputSchema` are screened at discovery,
after cleaning and before anything is admitted, because both are handed to a model
as tool definitions the moment an `mcp:` id enters `visible_tools(OPEN)`. The mark
goes on the description, since that is the string both audiences read; the kinds
ride on `DiscoveredTool.screened_kinds`. Nothing is dropped or refused there:
`_clean_schema` decides admission, and screening adds a sentence and reports kinds.

**The audit column: `tool_audit.screened`** (`agent_core/memory/store.py`, added
2026-08-13 by the existing add-column-if-missing path, so old rows read NULL and
mean "written before this column existed"). It carries the kinds, deduplicated and
sorted, exactly as `redacted` beside it does and for the same reason: the row is
durable and never pruned, so a quoted payload would live there forever.

## 2. How strong it is

**It is a backstop, not a boundary.** It is a pattern matcher over text somebody
else wrote. An injection phrased as ordinary prose, in a shape nobody has
enumerated in those six rules, passes untouched and unmarked. It reduces exposure
and does not eliminate it, and no document may describe it as elimination or as
prevention.

**The permission gate remains the only authority.** Nothing in screening decides
what may run. A flag is never a reason to skip a card, and an absence of flags is
never a reason to trust a passage. Every claim the gate made before 2026-08-13 it
still makes, unchanged and unweakened.

**The false-positive budget is part of the design.** A screen that marks everything
teaches the model to ignore the mark, so every rule requires text that genuinely
reads as an address to an assistant. That choice is what makes the rules narrow,
and narrow rules are what makes the paragraph above true.

## 3. Two findings from building it

**1. JSON escaping erases the anchors, so the leaves are screened and the
serialization is marked.** A tool result reaches the model through
`_result_as_text`, which is `json.dumps` for structured content, and `json.dumps`
turns a newline into the two characters backslash-n. That glues the "n" onto the
first word of the next line, taking with it the word boundary every rule anchors
on, and it collapses the whole document to one line, which costs every
line-anchored rule its line starts. A page whose injection opens a line survives
the escape perfectly readable to a model and invisible to every rule. So
`_screenable_text` walks the result and rejoins its string leaves (keys as well as
values) with real newlines for the screener, while the MARK goes in front of the
serialization the model is actually handed.

**2. The marker must take the caller's verdict, or a leaf-only finding is lost.**
`mark_untrusted` originally re-screened the text it was about to mark. Since the
caller screens one string and marks a different one, re-screening un-found what the
caller had just found: the model would read an unmarked injection while the audit
row said it was marked, which is the single combination the module exists to
prevent. The verdict is now a parameter, a caller that has one must pass it, and
the MCP path prefixes directly rather than going through the function at all,
because a schema-only hit is not present in the description being marked.

## 4. What is deliberately NOT built

- **No model-assisted second reader.** Detection is pattern-only. An adversary
  reviewer is a second model reading a stranger's text, which is a new cost, a new
  latency and a new surface, for a judgement no one has to trust.
- **No blocking, refusing, dropping or rewriting.** Screening never removes a
  passage and never fails a step.
- **No effect at the permission gate.** No auto-deny, no extra card, no tier
  change, no grant suppression.
- **No screening of a model's own output**, from any endpoint, free or paid.
- **No screening of local file reads or clipboard content.**
- **No user-facing setting.** It is always on, in every profile, and there is no
  Custom guard for it (see §5).
- **No surfacing of what was found beyond kinds.** Not in the note, not in the
  Activity Panel line, not in the audit row.

## 5. Owner decisions, 2026-08-13

Taken together on the day the work started, and recorded here in the owner's words
as decisions rather than as options.

1. **The v2 deferral is expired. Screening is pulled forward and built now.** Four
   triggers were written down against it and three had already fired. A deferral
   with its own trigger conditions met is not a deferral any more.
2. **Only tool-returned content is screened. Model OUTPUT from a free or
   gray-area endpoint is not.** The threat this answers is a stranger's document
   speaking to the model in the reader's turn. A model's own answer is a different
   problem with different controls, and folding the two together would produce a
   layer that is vague about both.
3. **Screening is always on, in every profile, with no Custom guard.** It is not a
   prompting guard and it will not become one. There is therefore no G4 anchor
   interaction: nothing here can be turned off, so nothing here mints an anchor.
4. **It is advisory only and changes nothing at the permission gate.** It adds a
   note and an audit column. It does not deny, does not escalate and does not
   relax. The gate is the authority, before and after.
5. **Local file reads and clipboard content are not screened for now.** They are
   the person's own material, reached through the person's own consent, and
   marking them would spend the false-positive budget where the threat is weakest.
   Revisit if either becomes a standing channel for a document the person did not
   write.

   > *Refined 2026-08-15 by owner decision 2A, which is this decision's revisit
   > condition being met rather than a reversal of it.* A shared routine file is
   > picked by the person, through the person's own consent, exactly like any other
   > local file. What makes it different is the half this decision turned on: it was
   > **written by somebody else**, which is the standing channel the last sentence
   > above asked to be told about. So import screens the picked file's text, and
   > ordinary local file reads and clipboard content are unchanged and still
   > unscreened. The cost is near zero, because a routine file is short and its
   > prose is a description.
6. **Detection is pattern-only. No model-assisted second reader.** Rules can be
   read, reviewed and tested; a second model's judgement can only be sampled.

## 6. What this does not close

The gaps this reduces rather than closes are named where they live, and
[KNOWN-GAPS.md](KNOWN-GAPS.md) owns them: the sandboxed command's deliberate
outbound network reach, and the two shapes a listed credential can still take past
the redactor. Screening puts a note in front of an instruction-shaped passage; it
does not stop a person approving a command, and it does not read a credential.
