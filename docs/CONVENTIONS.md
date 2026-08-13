# How work is done in this repository

**This file owns the working standard**: the bar a change has to clear, the
conventions established with the owner, and the facts about this machine you need
before running anything.

Read it once. `CLAUDE.md` is the rules for the *code*; this is the rules for the
*work*.

*(Extracted from `HANDOFF.md` on 2026-07-27, unchanged.)*

---

## Read this first: the standard this repo is held to

Two things happened this session that should change how you work here, and
neither is visible from the code alone.

**1. Green gates are not the bar.** The first build of the G3 floor passed all
eight gates while its headline requirement was broken (the one-action restore
walked the user back *into* the config they were escaping) because the tests
encoded the same wrong assumptions they were meant to catch. Since then every fix
in this repo carries a regression test **proven to fail when its own line is
reverted**, checked in a scratch copy outside the repo. Roughly 75 mutations have
been applied across the session; the ones that mattered are listed where they
belong. If you add a test, mutate the thing it guards and watch it go red. Three
tests that passed both before and after their own fix were caught and rewritten or
deleted this session; that is the failure mode, and it is not rare.

**2. Prose drifts from code, twice measured.** `CLAUDE.md` has twice asserted the
opposite of what shipped, once re-added by the very changeset that broke it.
Before trusting a sentence in any doc (including this one), check it against the
tree. Exact line counts and gate numbers are deliberately absent here for the same
reason; they went stale twice in a day, and a stale number reads as a claim.

This matters more here than in a repo people write, and the reason is worth stating
plainly: **a person senses a stale doc and an agent does not.** The tone being off,
the date being old, the sentence not quite matching what the code looks like: none
of that reaches a reader that has only the text. It reads the sentence as true and
acts on it. So a load-bearing fact gets registered in
[`tests/doc_claims.py`](../tests/doc_claims.py) rather than merely written down, and
a run names the file and line that disagree.

**A gate has two halves, and BOTH are asserted rather than remembered.** Mutating a
gate by hand proves it *can* fail, but that is a property of whoever last ran the
mutation, not of the suite, and a scanner whose pattern has drifted off the tree's
phrasing finds nothing while "found nothing" is also what a clean tree looks like. Nor
does mutating prove the gate stays quiet on prose the next honest edit would produce,
and quiet is what decides whether it is still here in a month: a gate that cries wolf
gets deleted by the next agent and takes its real coverage with it. So a documentation
gate ships with both halves beside it, feeding the same scanner content it must not
flag and content it must. [`tests/gate_precision.py`](../tests/gate_precision.py) owns
that convention, its two helpers, and the reason a false positive costs more here than
a missed one. Do not restate it; link.

## Writing a claim that will not go stale quietly

- **A measurement is not a property. Mark it perishable.** An empirical number is
  written with the date it was taken **and the conditions it was taken under**:

  ```
  29 ms *(measured 2026-07-31 · a warm read of an app-owned keychain item from the
  signing binary itself, on the owner's machine)*
  ```

  The exact form is `*(measured YYYY-MM-DD · what it was measured under)*`, with no
  parentheses inside it;
  `tests/test_docs_drift.py::test_every_measurement_marker_is_well_formed` checks the
  shape, and `test_a_spike_result_is_marked_perishable` refuses a spike figure that
  carries neither a marker nor a plain statement that it has been superseded. The
  **condition is the perishable half**: a number goes void because the thing it was
  measured under changed, and naming that thing is what lets the next reader tell.
  This repo quoted spike 1's keychain conclusion as a permanent property for six days
  after `sign-and-run.sh` had voided it.
- **A rule about one module belongs in that module's docstring.** Across these
  passes, the long docstrings sitting beside the code they govern drifted markedly
  less than the standalone narrative documents did; a docstring is in the diff that
  falsifies it, and a document three directories away is not. Write the rule where
  the code is and let the doc set link to it.
- **Write for grep, not for browsing.** A reader arrives by searching for a symbol,
  a filename or an error string, not by reading a file top to bottom. Put the literal
  name it will search for in the sentence that answers the question.

## Working conventions (established with the user)

- **Every change goes PR → `master` directly.** The stacked-PR era is over: no
  stacked chains, no long-lived branches. *(This line was nearly lost in the
  2026-07-27 documentation restructure: it lived only in a "where the project
  stands" bullet, which is status-shaped prose, so it was dropped when that bullet
  was rewritten. It is a convention, so it belongs here.)*
- **A commit that changes a documented rule amends the doc, in the same commit.**
  This is the defect the project has now shipped three times, once by re-adding
  the sentence its own changeset falsified, and once by leaving a branch-state
  paragraph standing through the merge that made it false. It is the reason the
  step-1 commit ledger existed; the ledger is retired and this rule replaces it.

- **Opus agents build, coordinator verifies.** Spawn Opus agents with EXACT,
  disjoint file-ownership lists; do shared-contract groundwork first (the
  hand-synced `agent_core/protocol.py` ↔ `shell/src/types/protocol.ts`; a drift
  test enforces sync, and a second fixture test now pins payload *shapes*); then
  personally verify the final tree (full suite, lints, pyright, builds, diff
  review of safety-critical code) before committing. Agent work survives a
  session death on disk, so inventory `git status` and finish inline.
- **For a subsystem this load-bearing, write the contract first.** Step 1 was
  built from a single frozen implementation contract, one file naming every
  method signature, every user-facing string, and every file's owner, adversarially
  reviewed before a line was written. Six parallel agents produced a tree that
  needed no reconciliation. The parts that earned their keep: a **frozen shared
  contract** section (names, signatures, exact copy) that no workstream may change
  unilaterally; **disjoint file ownership** with "report it, don't edit it" for
  anything outside your list; and a **doc-conflict resolution table** deciding each
  contradiction between existing docs *and saying why*, because the doc set had two
  rival schemas and nothing stated precedence. Reuse the shape for steps 5–8.
- **One PR per change, straight to `master`.** CI must be green.
- **Binding UI direction, the dark redesign (v4, adopted 2026-07-26).**
  `docs/design-brief-dark/` is authoritative for tokens, type, shape and copy:
  `README.md` + `prototype.html` are the designer's pixel-perfect reference, and
  **`IMPLEMENTATION.md` records the binding prototype→app mapping**; read it
  before restyling anything, because it is where "demo content is never shipped,
  real features are restyled and never de-wired" is written down. Near-black
  paper (`#0C0C0D`), hairline separators, one soft violet accent (`#B4A9F5`) for
  actions/selection/live state only; **system type only**: no bundled fonts, no
  `@font-face`, no serif. Shape: selection is a **2px accent left rail**,
  sections sit on 2px rules, rows are hairline-separated, and bordered panels are
  reserved for floating chrome (popover 7px, modal 8px, menu 6px), never cards, never
  pills. Signature motion: the character-scramble (`shell/src/lib/scramble.ts`)
  + fadeRise/fadeDrop, all no-ops under `prefers-reduced-motion`. Dark is the
  designed reference and light a derived translation; the theme stays three-way
  Light/Dark/Match-this-computer, default **Match this computer**. The mark is
  the lowercase-`a` tile (`AddisonMark.tsx`); the service bell is retired, and
  the OS icon set is regenerated from it via
  `docs/design-brief-dark/brand/build-app-icon.sh`. Plain language for personas
  54/68; never AI tropes or vendor branding.
  **This supersedes the "Fern" direction**; `docs/design-brief-fern/` stays in
  the tree as history and is authoritative for nothing.
- Verify UI changes in the browser preview where possible; note that the
  disconnected preview can't exercise the live core (no conversations, no skill
  persistence), so cover those with unit/component tests instead.
- The user starts every assistant message check with "Ad Astra." (memory).

## Environment facts

- **Keychain prompts were fixed by signing, not by code, and "Always Allow" now
  STICKS across rebuilds (verified 2026-08-06).** An unsigned `cargo build` is ad-hoc
  signed (`Signature=adhoc`, `TeamIdentifier=not set`, identifier embedding a
  per-build hash), and macOS binds an "Always Allow" decision to the code-signing
  identity, so each rebuild looked like a new app. **`shell/src-tauri/sign-and-run.sh`
  is the live mechanism**: a cargo *runner* that signs each dev build as `Addison Dev`
  with an EXPLICIT designated requirement and execs it, so a recompile presents a
  byte-identical requirement and the granted ACL keeps matching. It fails open on a
  machine without the identity (warns, runs unsigned). The one-time certificate
  creation (including the TRUST step people get stuck on) is in
  `scripts/sign-dev-binary.sh`'s header; that script is the superseded manual
  predecessor and does **not** set the designated requirement, so do not reach for it
  as the fix. Free: the $99 Apple Developer Program is for distribution (Gatekeeper),
  not for this. Within one process `KEY_CACHE` still collapses provider-key reads to
  one. Since 2026-08-06 a foreign item is also self-healed on the first successful
  read ([BUILD-LOG](BUILD-LOG.md)), so a prompt inherited from an older build is a
  one-off rather than a fixture of every session.


- Python venv: `agent_core/.venv` (pytest, ruff, httpx). **Note:** when working
  from a git worktree, run tests as
  `PYTHONPATH=$PWD /Users/karel/Desktop/Addison/agent_core/.venv/bin/python -m pytest tests/ -q`
  (the venv lives in the MAIN checkout).
- `ANTHROPIC_API_KEY` is exported in `~/.zshenv`. NEVER print it; check presence only.
- Dev knobs: `ADDISON_MODEL`, `ADDISON_DB_PATH`, `ADDISON_OLLAMA_URL`, `ADDISON_RELAY_URL`.
- Launch the app: `cd shell && npm run tauri dev` (first Rust build is slow).
  A backend change needs a **restart**, not just Cmd+R.
- **The gate list is `scripts/gates.sh`**, not a paragraph here or in
  [`VERIFICATION.md`](VERIFICATION.md). Run `./scripts/gates.sh` and CI runs the
  same script. Reporting green against a remembered subset is what made it a program.
