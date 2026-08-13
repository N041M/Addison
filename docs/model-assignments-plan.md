# Model assignments: which model does which kind of work

**Status: PROPOSED 2026-08-09. Not scheduled.** [`ROADMAP.md`](../ROADMAP.md) owns
whether that changes; this document owns the design. It grew out of the
2026-08-09 feature-suggestion review ([`KNOWN-GAPS.md`](KNOWN-GAPS.md) records the
judged shapes) and it deliberately **folds three things that were heading for
three separate designs**:

1. **Per-task model assignment** (owner idea, 2026-08-09): one model drives tool
   use (say, a web search) and a different one interprets what came back, with a
   possible drag-and-drop rail as the Developer surface.
2. **The Developer custom chain**, which is already the manual end of routing;
   assignments generalize "one ordered chain for everything" toward "a choice per
   kind of work" without replacing it.
3. **The v2 auto-routing substrate.** `providers/router.py`'s own header says v2
   automatic routing will call `resolve()` with a choice Addison made: deferred
   *decision*, existing *machinery*. Assignments give that future decision named
   slots to fill instead of raw model picks, so the classifier plugs in later
   rather than beside.

The economic case is the 2026-08-09 web-search cost discussion: the search
backend is free (DDG HTML endpoint, no key) and the entire cost of a search
interaction is model tokens, and the *biggest* token load is the call that reads
tool output (`read_web_page` returns up to 20,000 characters). Routing exactly
that call to a cheap or local model is where the money is; everything else here
is the machinery to say so safely and explicitly.

## 1. What exists: the substrate this builds on

- **`ModelRole` is a provider POOL, not a kind of work**: `PRIMARY | LOCAL |
  SETUP_ASSISTANT`. This plan does not touch it, and deliberately does not
  overload it; see the naming decision in §2.1.
- **`ModelRouter.resolve(requested_role, model_name)` runs per request**: there
  is no `self.active_provider` (CLAUDE.md convention). Explicit only in v1: a UI
  pick or a Routine step's pin.
- **Routine steps already pin** `model_role` / `model_name` per step. Assignments
  must compose with that, not compete (§3.4).
- **Strategies order the fallback chain** (`resolve_chain`, a pure function), the
  orchestrator applies cooldown over it, and the chain HEAD is frozen to today's
  resolution so the happy path is byte-identical to the pre-strategy behaviour.
  That freeze is the design idiom this plan copies: **an unset assignment changes
  nothing, byte-for-byte** (§3.2).
- **Mid-turn advance is provider-bounded**: router.py's [MF-B] forbids
  cross-provider advance once a tool round has run (provider-specific tool-call
  history is the reason), while [MF-E] permits it among Ollama locals because they
  share one translator. Any call that runs *after tool results* (which is exactly
  the call assignments most want to re-route) lives inside that boundary (§3.3).

## 2. The design

### 2.1 Duties: a closed set, decided structurally

A **duty** names a kind of model call. The set is **closed and hard-coded**, the
widget-kinds lesson (SAFETY.md invariant 4): a closed list the code owns beats a
declaration anything else supplies. Three duties at birth:

| Duty | The call it names | How it is decided |
|---|---|---|
| `CHAT` | The first model call of a live turn | Structural: turn start |
| `INTERPRET` | Every model call in the same turn *after* tool results have entered it | Structural: follows a tool round |
| `ROUTINE` | A replayed routine step's model call | Structural: the engine is the caller |

**"Decided structurally" is load-bearing.** No text is classified, no difficulty
is judged; the duty of a call is a fact about *where the loop is*, not about
what the user meant. That keeps this inside v1's "routing is EXPLICIT only" rule:
the person chooses the model for a named slot; Addison never chooses a model, it
only reports which slot a call sits in. The v2 classifier, when it comes, is
the thing that gets to be clever, and it fills these same slots.

Named `duty`, not `role`, because `ModelRole` is taken and means pools. A duty
maps to (role, model) pairs through the assignment table; the two axes stay
orthogonal.

**Deliberately not in the set:** a `CODING` duty (a Developer chat turn does not
declare itself as code work; deciding that is classification, which is v2), and
a `HOUSEKEEPING` duty (the v2 context condensation would want one; reserve the
concept, mint the name when there is a caller).

### 2.2 Resolution: assignments override, absence is invisible

For a call with duty D:

1. If an assignment exists for D (a `(role, model_id)` the person set), resolve
   through it. If that model is unavailable (cooldown, failure), **degrade along
   the active strategy exactly as any resolution does today**, with the existing
   activity note. An assignment is a preference, never a hard pin; `local_only`
   remains the only hard filter, and it keeps winning (an assignment naming a
   cloud model under `local_only` is skipped with a note, because the strategy's
   privacy promise outranks a convenience setting).
2. If no assignment exists for D, resolve exactly as today. **With zero
   assignments set, every code path is byte-identical to current behaviour**; a
   test pins this the way the head-freeze tests pin D3.

Simple never sees or sets assignments, so in Simple the table is empty and rule 2
is the whole story. That is how the profile boundary is kept without a single
`if profile == ...` in the router.

### 2.3 The mid-turn boundary: the honest limit, stated up front

`INTERPRET` fires mid-turn by definition, so it collides with [MF-B]: the
conversation so far holds tool-call history in the acting provider's format, and
handing it to another vendor mid-turn is not implemented.

**v1 honours an INTERPRET assignment only when it shares `provider_id` with the
turn's CHAT resolution**: Opus-class → Haiku-class on one key, or one Ollama
local → another ([MF-E] already blesses that pair). Cross-vendor (Anthropic
CHAT → Ollama INTERPRET), which is the *biggest* saving, is refused with one
activity note naming why, and recorded as the follow-up that needs
provider-agnostic mid-turn history translation. That is real work on the
orchestrator's most delicate seam; it is not smuggled in here as a side effect.

Second honest limit: the INTERPRET model may emit *further tool calls*; the
loop cannot know a synthesis call is final until it is. They stay INTERPRET
until the turn ends. A weak interpret model making poor follow-up tool decisions
is a real failure mode; the mitigation is visibility (§2.5), the same answer the
free-model disclaimer already gives, plus `ProviderCapabilities`: a model that
cannot drive tools at all is already the capability system's case, never an
`isinstance` branch.

### 2.4 Composition with what already pins

A Routine step's explicit `model_role` / `model_name` **wins over** the `ROUTINE`
assignment, because a pin on the step is more specific than a default for the duty.
The assignment is the answer to "routines run cheap unless a step says
otherwise," which today has no home.

### 2.5 The person can always see which model answered and why

Every assignment-driven resolution emits the existing activity-note shape,
extended by one clause: *"Answered with X (your interpret model)"*. The
free-model disclaimer is unchanged and still fires on top when it applies. No
hidden decisions (design-doc §9): a person who sets an assignment and then
forgets it must be able to read, in the thread, why a different model answered
the second half of a turn.

### 2.6 Persistence and the floors

A small table, `model_assignments(duty PRIMARY KEY, model_role, model_id)`,
captured by config snapshots, so setting or clearing one is **reversible config
under G3** like endpoints and strategies. Non-secret metadata only; G1 is not in
the story. No floor moves: assignments never widen what a tool may do, never
touch the gate, and the SAFE view never changes; an empty table *is* the SAFE
behaviour.

### 2.7 Surface

Developer and Custom profiles (both OPEN; this is routing config, not a guard,
so it does not enter the Custom guard panel). **v1 surface: a "Model
assignments" Settings section**: one row per duty, each carrying the folder-tree
model picker that already exists, plus a "None (use my strategy)" default. The
**drag-and-drop rail** (a sidebar strip of chosen models dropped onto duty
slots) is the owner's wanted end state and ships as a later polish phase on top
of the same RPC: the section is the mechanism, the rail is a nicer hand on the
same lever. In Simple the section does not render, the same way the MCP section
does not: a Settings surface for a capability a profile lacks is profile
surface, not a disabled artifact (the listed-but-disabled rule is about work a
person made; nobody's work is hidden here).

## 3. Deliberately not in this plan

- **No classifier.** Duties are structural. The v2 task classifier maps
  task → duty/model later, on top of this table, and stays overridable and
  visible per router.py's header.
- **No per-duty chains.** An assignment is one model; failure degrades along the
  active strategy. Chain-per-duty is a v2 shape if wanted; the custom chain
  stays global until then.
- **No cross-vendor mid-turn handoff** (§2.3). Named follow-up, not scope.
- **No new duties without a caller.** The set grows by code review, not by
  configuration.

## 4. Build order, when scheduled

1. **Substrate.** The `Duty` set; `resolve()` learns an optional duty (default
   `CHAT`); the table + G3 capture + Developer-gated RPC get/set. The
   byte-identical-when-empty test lands here, first.
2. **INTERPRET**, same-provider only, with the activity note and the [MF-B]
   refusal note. This phase alone delivers the cost win (§ economics above).
3. **ROUTINE** as the default for unpinned steps.
4. **The Settings section**; then the rail as polish.

Later options, recorded not promised: cross-vendor INTERPRET via history
translation; chain-per-duty; a CODING duty once something structural can decide
it; the v2 classifier writing into these slots.

## 5. Open questions for the owner

1. **Same-provider-only INTERPRET for v1.** Accept the limit (recommended;
   Opus→Haiku-class on one key is most of the saving), or wait for history
   translation and ship INTERPRET later but unrestricted?
2. **Does the rail justify its build cost**, or is the Settings section enough
   until the feature earns usage? (Recommended: section first, rail on evidence.)
3. **Naming in the UI.** "Assignments" / "duties" is internal vocabulary; the
   user-facing strings need the plain-language pass (CLAUDE.md conventions),
   e.g. *"Answering you"*, *"Reading tool results"*, *"Running routines"*.
