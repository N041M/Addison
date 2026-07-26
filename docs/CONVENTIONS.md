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
eight gates while its headline requirement was broken — the one-action restore
walked the user back *into* the config they were escaping — because the tests
encoded the same wrong assumptions they were meant to catch. Since then every fix
in this repo carries a regression test **proven to fail when its own line is
reverted**, checked in a scratch copy outside the repo. Roughly 75 mutations have
been applied across the session; the ones that mattered are listed where they
belong. If you add a test, mutate the thing it guards and watch it go red. Three
tests that passed both before and after their own fix were caught and rewritten or
deleted this session — that is the failure mode, and it is not rare.

**2. Prose drifts from code, twice measured.** `CLAUDE.md` has twice asserted the
opposite of what shipped, once re-added by the very changeset that broke it.
Before trusting a sentence in any doc — including this one — check it against the
tree. Exact line counts and gate numbers are deliberately absent here for the same
reason; they went stale twice in a day, and a stale number reads as a claim.

## Working conventions (established with the user)

- **Every change goes PR → `master` directly.** The stacked-PR era is over: no
  stacked chains, no long-lived branches. *(This line was nearly lost in the
  2026-07-27 documentation restructure — it lived only in a "where the project
  stands" bullet, which is status-shaped prose, so it was dropped when that bullet
  was rewritten. It is a convention, so it belongs here.)*
- **A commit that changes a documented rule amends the doc, in the same commit.**
  This is the defect the project has now shipped three times — once by re-adding
  the sentence its own changeset falsified, and once by leaving a branch-state
  paragraph standing through the merge that made it false. It is the reason the
  step-1 commit ledger existed; the ledger is retired and this rule replaces it.

- **Opus agents build, coordinator verifies.** Spawn Opus agents with EXACT,
  disjoint file-ownership lists; do shared-contract groundwork first (the
  hand-synced `agent_core/protocol.py` ↔ `shell/src/types/protocol.ts` — a drift
  test enforces sync, and a second fixture test now pins payload *shapes*); then
  personally verify the final tree (full suite, lints, pyright, builds, diff
  review of safety-critical code) before committing. Agent work survives a
  session death on disk — inventory `git status` and finish inline.
- **For a subsystem this load-bearing, write the contract first.** Step 1 was
  built from a single frozen implementation contract — one file naming every
  method signature, every user-facing string, and every file's owner, adversarially
  reviewed before a line was written. Six parallel agents produced a tree that
  needed no reconciliation. The parts that earned their keep: a **frozen shared
  contract** section (names, signatures, exact copy) that no workstream may change
  unilaterally; **disjoint file ownership** with "report it, don't edit it" for
  anything outside your list; and a **doc-conflict resolution table** deciding each
  contradiction between existing docs *and saying why*, because the doc set had two
  rival schemas and nothing stated precedence. Reuse the shape for steps 5–8.
- **One PR per change, straight to `master`.** CI must be green.
- **Binding UI direction — the dark redesign (v4, adopted 2026-07-26).**
  `docs/design-brief-dark/` is authoritative for tokens, type, shape and copy:
  `README.md` + `prototype.html` are the designer's pixel-perfect reference, and
  **`IMPLEMENTATION.md` records the binding prototype→app mapping** — read it
  before restyling anything, because it is where "demo content is never shipped,
  real features are restyled and never de-wired" is written down. Near-black
  paper (`#0C0C0D`), hairline separators, one soft violet accent (`#B4A9F5`) for
  actions/selection/live state only; **system type only** — no bundled fonts, no
  `@font-face`, no serif. Shape: selection is a **2px accent left rail**,
  sections sit on 2px rules, rows are hairline-separated, and bordered panels are
  reserved for floating chrome (popover 7px, modal 8px, menu 6px) — no cards, no
  pills. Signature motion: the character-scramble (`shell/src/lib/scramble.ts`)
  + fadeRise/fadeDrop, all no-ops under `prefers-reduced-motion`. Dark is the
  designed reference and light a derived translation; the theme stays three-way
  Light/Dark/Match-this-computer, default **Match this computer**. The mark is
  the lowercase-`a` tile (`AddisonMark.tsx`); the service bell is retired, and
  the OS icon set is regenerated from it via
  `docs/design-brief-dark/brand/build-app-icon.sh`. Plain language for personas
  54/68; never AI tropes or vendor branding.
  **This supersedes the "Fern" direction** — `docs/design-brief-fern/` stays in
  the tree as history and is authoritative for nothing.
- Verify UI changes in the browser preview where possible; note that the
  disconnected preview can't exercise the live core (no conversations, no skill
  persistence) — cover those with unit/component tests instead.
- The user starts every assistant message check with "Ad Astra." (memory).

## Environment facts

- **Keychain prompts on every rebuild — fixed by signing, not by code.** Dev builds
  are ad-hoc signed (`Signature=adhoc`, `TeamIdentifier=not set`, and the identifier
  embeds a per-build hash), and macOS binds an "Always Allow" keychain decision to
  the code-signing identity. So each `cargo build` looks like a new app and the
  saved decision stops matching. `scripts/sign-dev-binary.sh` signs the dev binary
  with a stable self-signed certificate; the one-time certificate creation is in its
  header. Free — the $99 Apple Developer Program is for distribution (Gatekeeper),
  not for this. Within one process `KEY_CACHE` already collapses provider-key reads
  to one, so repeated prompts mean repeated rebuilds, not a cache miss.


- Python venv: `agent_core/.venv` (pytest, ruff, httpx). **Note:** when working
  from a git worktree, run tests as
  `PYTHONPATH=$PWD /Users/karel/Desktop/Addison/agent_core/.venv/bin/python -m pytest tests/ -q`
  (the venv lives in the MAIN checkout).
- `ANTHROPIC_API_KEY` is exported in `~/.zshenv`. NEVER print it; check presence only.
- Dev knobs: `ADDISON_MODEL`, `ADDISON_DB_PATH`, `ADDISON_OLLAMA_URL`, `ADDISON_RELAY_URL`.
- Launch the app: `cd shell && npm run tauri dev` (first Rust build is slow).
  A backend change needs a **restart**, not just Cmd+R.
- **The gate commands live in `VERIFICATION.md` §1**, not here.
