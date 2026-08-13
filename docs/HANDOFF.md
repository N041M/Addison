# Addison: session handoff

**Where things stand right now, and what to pick up.** Nothing durable lives here.
This file is expected to go stale and be rewritten. Everything that should outlive a
session has its own owner:

| You want | Read |
|---|---|
| The rules for the code | [`../CLAUDE.md`](../CLAUDE.md) |
| Floors, modes, guards, snapshots | [`SAFETY.md`](SAFETY.md) |
| What is built / next / not being built | [`../ROADMAP.md`](../ROADMAP.md) |
| Live issues, open design questions | [`KNOWN-GAPS.md`](KNOWN-GAPS.md) |
| The standard, conventions, environment | [`CONVENTIONS.md`](CONVENTIONS.md) |
| What each step shipped + what its rigor pass found | [`BUILD-LOG.md`](BUILD-LOG.md) |
| Gates, live-driver proofs, diff review | [`VERIFICATION.md`](VERIFICATION.md) |

**Start with `CONVENTIONS.md` if you have not worked here before.** The bar in it is
unusual and green gates are explicitly not it.

---

## Before you touch anything

```bash
./scripts/gates.sh          # every gate, exactly as CI runs them
```

**That script is the gate list.** `ci.yml` calls the same one, so there is no second
copy to disagree with. Do not run a remembered subset: a whole session on 2026-08-06
reported "all gates green" having never run pyright or ESLint, and writing the fix
found that CI had *never once* run the test-file typecheck, and that `clippy` was not
a gate at all.

Two things it cannot check, both learned the hard way the same day:

- **Platform-gated code.** `#[cfg(target_os = "macos")]` compiles here and vanishes
  on CI's Linux runner, taking its imports and constants with it, so `-D warnings`
  finds dead code there that does not exist here. Cross-checking locally is not
  practical (a Linux build of the Tauri deps needs a webkit sysroot). When you gate a
  symbol, check everything it was the sole user of.
- **Anything resolved from outside the repo.** `tsconfig.test.json` passed locally for
  weeks because TypeScript found `@types/node` in `/Users/karel/`, above the project
  entirely. A gate green for a reason that is not in the repository is worse than a
  gate that is red.

## Next up

**NOTHING FROM THE PHASE-2 SEQUENCE REMAINS. Step 8 completed 2026-08-08
(phases 1–3 on 2026-08-07, phase 4 the next morning), and with it the July-2026
scope change.** What is next is **Phase 3**
(packaging, signing, notarisation, the auto updater, previous-binary restore,
Secure Enclave identity). The **Developer review surface**, the phase's second track,
is **BUILT: all five of its plan's Build sections landed on 2026-08-08**, and the only
thing left on it is a manual pass (see the next paragraph).
`ROADMAP.md` owns status; trust it over this file.

**START HERE ON THE REVIEW SURFACE: the §13c manual pass, not more code.**
`TESTING-CHECKLIST.md` §13c is live and unrun. It matters more than a checklist usually
does, because this wave widened the webview's content-security policy (two
directives against four tightenings) and **a CSP is enforced by a real webview and
by nothing else**. The widenings are `style-src 'unsafe-inline'`, which Monaco
genuinely cannot run without, and `img-src 'self' data:`, which the previous policy
(literally `default-src 'self'` and nothing more) refused. The build
could not run one. `tests/test_csp_is_pinned.py` holds the authored string and the two
structural rules (`script-src` never admits `'unsafe-eval'`/`'unsafe-inline'`; no
directive admits `*`, `http:`, `https:` or `blob:`), and `shell/src/lib/cspReport.ts`
reports every violation into the Developer diagnostics ring permanently, so §13c is a
matter of opening that panel, using the screen properly on all three platforms, and
confirming it stays empty. The bright line, if it does not: **do not widen `script-src`
or admit `blob:`.** The fallback the plan names is the `<pre>` + highlight.js viewer.
Everything else outstanding is the plan's follow-up list, which is now one item: an
editor zoom control (the 12px type size is recorded as a tension, not a settled
answer). The other two landed on 2026-08-13, JSON highlighting, and the post-restart
revert case via `shell.adoptWorkspacePath`.

**The review surface's first prerequisite is now done (2026-08-08).** The two file
tools' `permission_detail` read the RAW `path` argument while `affected_path`
resolved, so the name a person was shown and the file Addison touched were two
different answers whenever a symlink sat between them, and inside a trusted root
(`notes.txt` → `secrets.env`, both in trust) nothing refuses that, so the displayed
name was the only thing standing there and it named the decoy. Both now implement
`permission_detail_for_path(resolved_path)` and never see `args` at that seam, so
they are handed the caller's single resolution and structurally cannot make a second
one: the card, the Activity Panel and the boundary cannot name three different
files. (An intermediate version of this fix had both tools call
`call_affected_path` themselves; that was superseded on the same day, because two
call sites resolving separately is the defect, not the fix. See the paragraph on the
name race further down.) Basename-only is unchanged;
resolving is what turns a bare argument INTO a full path, so this had to keep the
`.name`. **All three of the plan's prerequisites are now closed, each in its own PR on
2026-08-08**: this one, the missing read ceiling in `filesystem.rs`, and the prune
wiring below. [`phase-3-review-surface-plan.md`](phase-3-review-surface-plan.md) owns
all three and what each cost.

**And the plan's docs-first phase landed the same day, so the next change on that
track is code.** Amendment §14 asks for authoritative docs before any surface, and
that is now discharged: the Phase-3 redefinition is stated in every document that
defines the phase (`architecture.md` had never received it and `ROADMAP.md` had grown
its own packaging-only definition since), the two sentences this wave strains are
answered where they live, the design mapping for a code surface is the last section of
`design-brief-dark/IMPLEMENTATION.md`, and the manual pass is TESTING-CHECKLIST
**§13c**. A `phase-3-includes-the-review-surface` row in `tests/doc_claims.py` now
fails the suite on the sixth document to define Phase 3 as packaging alone.

**Build §1 through §5 all landed on 2026-08-08.** §1 shipped the read paths: `workspace.listDirectory` and
`workspace.readFile` as RPC and never registry tools, two Rust bridge methods beside
the step-5 block, and the four-step confinement order (mode gate, resolve once,
trusted-root check, pass only the resolved value). The plan's §1 now carries what
shipped and the decisions taken while building it; the two worth knowing are
that a refusal answers `{ok: false, error}` while a success carries no `ok` at all, and
that these paths are **absolute-only**, because `realpath` would otherwise quietly
complete a
relative path against the core process's working directory. Each section shipped its
TypeScript types and generated fixtures ahead of a consumer, and §4's parsers now run
over those same fixtures in `workspaceReadPaths.fixtures.test.ts` /
`workspaceEdits.fixtures.test.ts`, which is the `roots`/`folders` drift loop finally
closed on both sides rather than one.

**§2 and §3 shipped the data and the sharp edge.** `workspace.listEdits` (metadata only)
`/readEditDiff` / `revertFile`, a `wrote_sha256` on every write's undo payload, and a
THIRD revert mechanism in `agent_core/snapshots/file_revert.py`: per-path,
out-of-order, chain-collapsing, `write_project_file`-only, beside `UndoManager` and
`SnapshotManager` and calling neither. Four things §4 had to honour, and did:

- **Reverting a file settles its WHOLE unreverted chain in one write**, landing on the
  oldest prior (a state that actually existed) so zero unreverted rows remain and the
  undo button cannot resurrect what was reverted away from. The diff's BEFORE pane is
  that same oldest prior, so Revert produces exactly what is on screen.
- **`onDiskChanged` is tri-state.** `null` means Addison cannot tell (a row from before
  the digest, or a file the shell cannot judge) and must render as that: `false` is the
  value that lets a revert proceed with no warning.
- **`revertable` is the shell's answer about its SESSION write ledger**, asked through a
  new pure query (`shell.canRestoreWorkspaceFiles`) with no filesystem effect. After a
  restart it is false for every historic edit, and §4 renders those read-only with the
  plan's plain line rather than a button that fails. `undo.undoLastAction` now has
  the same honesty, asked the same way, and marks nothing when the answer is no.
- **One new Rust method beyond the plan's list: `shell.digestWorkspaceFiles`.** The core
  has no filesystem of its own, and hashing each file core-side would ship the megabytes
  `listEdits` is metadata-only to avoid. Both new shell methods are batches answering a
  MAP keyed by path, never an array positioned against the request.

**§4 and §5 shipped the screen and the skin**, and the plan was wrong about the tree in
two places that are worth knowing because they will mislead the next reader of it too:
there is no `screen` state (`App.tsx` holds `view: View` and the union already had five
members, not two) and the Escape handler already read `view !== "chat"`. The third call
site the plan warned about was real: widening the type without a Sidebar entry leaves a
screen with no way to reach it, so the nav row exists and **its gate is the handler**.
App passes `onOpenCode` only under Developer/Custom, and the Sidebar renders no row
without one. The screen is gated a second time at the render, because a profile can
change under an open screen. Monaco is loaded from the ESM API entry only (in monaco
0.56 that is `monaco-editor/editor/editor.api`, not the `esm/vs/…` path the plan names,
because the package gained an `exports` map), its worker is bundled with a plain
`?worker`, and
its theme is built from the `--hl-*` variables `styles.css` already carried, so the repo
has one code palette rather than two. The editor is a lazy chunk nobody who avoids the
screen ever downloads: measured against a build of master, the initial bundle grows by
23 kB (7 kB gzip), which is the screen's own code and none of it Monaco.

`action_snapshots` also got **its first index** (`idx_action_snapshots_tool_reverted`), a
line in `schema.sql`'s index block, because retention now collects reverted rows only and
the surface's query reads precisely the subset nothing bounds.

**And the name race closed with it** (KNOWN-GAPS): the card, the Activity Panel, the
audit row and the effect now come from ONE resolution per call. A path-bounded tool
implements `permission_detail_for_path(resolved_path)`, and it no longer sees `args` at
that seam, so it cannot resolve a second time. The orchestrator and routine engine
resolve above their refusal branches, which is where the second realpath actually lived:
the denylist and arming rows name a file too, and they were re-resolving as well.

Found while fixing it, and fixed with it: **`call_affected_path`'s except tuple did
not name `RuntimeError`**, which is what `Path.expanduser()` raises for a `~someone`
the OS cannot look up. That is the same crash the NUL case was fixed for in step 5;
these call sites sit outside the per-call error handling, so the turn died instead of
the step, and a routine run was left recorded `running` forever, reachable by one
model-authored `read_project_file{path:"~someone-unknown/x"}`, and missed because the
tuple listed the three exceptions `resolve()` raises and none of `expanduser()`'s.

**The third prerequisite, `UndoManager.prune()`'s zero call sites, needed an owner
call, and got one on 2026-08-08: the recency arm applies to REVERTED rows only, the age
arm stays as its co-condition, and bounding `listEdits` belongs to the surface build.**
Wiring the prune as it was written would have been worse than leaving it unwired: it
spanned reverted and unreverted rows alike, so the first launch after a busy week would
have deleted the very rows that describe changes still sitting on disk, and the review
surface would list fewer edits than exist and offer no way back from the ones it
dropped. The call site is `main.JsonRpcServer._ensure_built`: §4.5 asks for "on startup",
and that IS the startup. The worker thread builds once before it dequeues anything, all
store access is confined to it, so no undo is in flight and no `record()` can race. It
is the mirror image of `SnapshotManager`, which prunes inside capture and pays for it
with a `prune=False` escape.

Two things to know before touching it. **The unreverted set is now bounded by nothing**:
one row per live edit, forever. That is the accepted cost, not an oversight: deletion
is retention's only instrument and for these rows deletion is the harm. If it ever needs
a bound, that bound is a *reconciliation* (the file is gone, the prior no longer applies)
and never a recency prune. And **the keep-set is computed over reverted rows too**, not
just the delete, because otherwise a burst of live edits silently evicts old reverted
rows from
the window, which is the same bug wearing a different hat. `tests/test_undo_manager.py`
kills both mutations by name.

- **8: the automation keyword gate. COMPLETE: phases 1–3 landed 2026-08-07 and
  phase 4 on 2026-08-08** ([`step-8-automation-plan.md`](step-8-automation-plan.md) owns the
  phases and the decisions). Syntax was decided by the owner (a per-automation
  nonce Addison shows and you retype, because a fixed prefix is forgeable by
  anything that can write English); the plan settles what it gates (ARMING only),
  what automation IS in v1 (launchd user agents, macOS-only arming, typed shell
  surface that builds its own plist), and what a restore may never do (re-arm;
  no armed column exists, structurally). **Phase 1 = the fence + the inert
  table** (the eleven `OS_AUTOMATION_DIRS` un-trustable/denylisted/write-denied,
  lockstep-tested across Python and Rust). **Phase 2 = authoring**:
  `create_automation` (dev-only, MEDIUM, real undo, registered `open_only` so the
  undo check stays ENFORCED) with a four-refusal door (schedule bounds, the
  dispatch denylist asked at authoring, where a draft whose command is `crontab` is
  refused as arming, secret shapes via the redactor, ASCII-folded unique labels),
  a chat-only plist preview (`plist_text` may never cross IPC; a source test
  pins the rpc layer cannot import it), `scheduleSentence` on the wire, and the
  Developer-only Settings drafts section. **Phase 3 = the gate and arming
  themselves**: `automation_nonce.py` (six characters, lookalikes removed,
  constant-time compare, three attempts), the shell's `automation.rs` (the only
  writer of `~/Library/LaunchAgents`; it builds the plist itself from typed fields
  and never takes a document), `arm_automation` (HIGH, real `undo()` = disarm) and
  `disarm_automation` (a tightening: ordinary card, no code, no undo). **Phase 4 =
  state honesty**: armed truth is asked of
  the OS when a surface loads and never stored (a restore puts a ROW back, never a
  JOB), Simple lists automations as disabled rows instead of hiding them, and
  `App.tsx`'s `onRestored` re-reads the list like every other captured table.

  **Four things to know before touching this subsystem**, three from the phase-3
  review and one from phase 4:

  - Phase 4's disabled marker is decided from what an automation IS (it runs a
    command, so always) via a literal `True`, NOT from `created_in_mode`. A branch
    scan pins it. Routines were the cautionary case and were converted on
    2026-08-08 (they ask `_routine_needs_dev`, a per-row question); this table has no
    per-row question at all, so keep the literal.
  - The ceremony's requirement lives on the TOOL (`gate.tool_requires_arming`),
    never on whether a preview arrived. Keyed off the payload it failed OPEN,
    downgrading to an ordinary card, or under Custom's "never ask" to none.
  - `automation.remove` disarms BEFORE it forgets, because a removed row leaves a
    running job nobody can name or stop.
  - The core and the shell are two implementations of one contract, pinned in
    `tests/test_automations.py` for BOTH the plist bytes and the label rules. The
    label half was missing and had already drifted.
- **7: MCP client. DONE FOR v1: phases 1–4 of five, 2026-08-06 to 2026-08-07.**
  Transport was decided by the owner on 2026-08-06: **HTTP only for v1**, which is
  what keeps the client in the Agent Core and adds no new highest-trust surface.
  Configuration, then connect + discovery (`agent_core/mcp_client.py` speaks the
  protocol, `agent_core/mcp_catalog.py` admits what it finds to the ONE registry
  namespaced and dev-only), then dispatch through the ordinary gate as HIGH and
  destructive with `tool_audit` on every outcome, then output handling. **Phase 5 is
  a recorded later option, not a missing piece**: stdio under containment, and SAFE
  admission via a promoted allowlist. If you are picking this subsystem up, read the
  plan's §4.2 scoping decisions first (no auth, on-demand only, catalog in memory):
  each is a thing the next phase may want and must decide again rather than inherit.
  Plan: [`step-7-mcp-plan.md`](step-7-mcp-plan.md).

**The keychain thread is half done.** Plan steps 1–2 and §5.2/§5.3 shipped; steps 3–5
(`Intent`, launch reconciliation, the shipped read counter, the cards) have not.
Read [`secrets-and-keychain-plan.md`](secrets-and-keychain-plan.md) before touching
`keychain.rs`. **`FAILED_READS` survives on purpose**: its presence role is gone, but
two background callers still fetch key *values*, and deleting it now would let a
launch task re-raise a dialog somebody had dismissed.

**Two queued, contract-first, not started:** rework local-model setup (state-aware:
not-downloaded → one-click download plus a source link; downloaded → how to connect
it; and more open-source models), and skills file-upload (an uploaded text file's
contents become the skill's guidance text, editable, previewed, size-limited).

## What changed on 2026-08-07, in one paragraph each

Four PRs merged (#60–#63), then a review of all four found about twenty-five real
defects and they were fixed the same day, and then the step-8 plan was written and
its phase 1 built. `BUILD-LOG.md` owns the findings; these are
the ones that change how you should read the tree.

- **Phases 1–2 were then reviewed, and the review's own fixes were reviewed
  again.** Four read-only reviewers over disjoint scopes, then an adversarial pass
  over the fixes, which found three regressions the fix round had introduced, one
  wider than the defect it fixed. `BUILD-LOG.md` owns the findings. The two worth
  knowing before you touch this subsystem: **`plist_text` had no real test at all**
  (its only assertion compared the function against itself, so dropping its XML
  escaping passed 1449 tests), and **the arming fence's blast radius is not what a
  comment says it is**, because a step-over meant for `VAR=value` chained through any
  `=`-bearing word until it was caught and narrowed.
- **Step 8 has a plan and all four phases are in.** See "Next up" above for the
  whole shape. What changes how you read the tree: `~/Library`, `~/.config` and
  the eleven OS-automation directories are no longer trustable workspaces,
  commands naming them (or invoking `launchctl`/`crontab`/`at`/`batch` as a first
  word) are refused pre-gate with their own sentence, `policy.trust_refusal` is
  the reason-reporting form of `workspace_trust_allows`, and the `automations`
  table fills ONLY through `create_automation` (dev-only, four-refusal door).
  There is still **no armed column** (armed-ness is read back from the OS on
  demand) and arming goes through `arm_automation` behind a code the person
  retypes.

- **Step 7 is COMPLETE for v1.** Phases 2, 3 and 4 all landed: a tool server's tools
  are discovered, callable through the ordinary gate, and what one answers is
  redacted, bounded and disclosed rather than filtered. Simple sees none of it.
- **The two model pickers are a folder tree**: company, then family, then model, one
  folder open at a time, drawn by the composer menu and the Settings popup from one
  engine (`shell/src/lib/modelGroups.ts`) so they cannot disagree. Owner decision.
- **The review's two biggest finds are worth knowing before you touch either file.**
  The `tool_audit` rebuild could strand every audit row permanently if it was
  interrupted; and the structured channel's redaction ran after serialization, which
  turned it off for any credential a control character had split, and made the audit
  row report no leak on the call where the leak happened. Both fixed; both were in
  code merged hours earlier.
- **Two redaction gaps are recorded rather than closed**, deliberately: a credential
  split by a newline, tab, quote or backslash, and a fullwidth/homoglyph one.
  `KNOWN-GAPS.md` owns both, with what each costs. The redactor is a backstop, not a
  boundary, and nothing may be built on it having seen everything.

## What changed on 2026-08-06, in one paragraph each

The tree was carrying ~30 uncommitted files at the start of the day. All of it is
committed and pushed; `master` is clean.

- **Step 6 is COMPLETE, both halves.** Dev-made routines and widgets are now listed
  in Simple as disabled rows that say why, instead of vanishing. That changed a
  documented rule, and `SAFETY.md` owns the new one. Simple also gained three
  interactive widget kinds (checklist, note, timer), with what you have *done* with a
  widget kept in a separate `widget_state` table from what the widget *is*, excluded
  from snapshots so a restore never unticks your list. The capability-declaration
  lattice was **cut, not deferred**: the closed kind list is the same gate with
  nothing to get out of step.
- **The audit trail was leaking secrets.** `tool_audit.detail` stored the raw command
  text, so an exported key landed verbatim in the one table excluded from snapshots
  and never pruned. Redacted at the single write, and the test that was supposed to
  catch it was vacuous: its tool defined no `permission_detail`.
- **Four sandbox holes closed.** A sandboxed command could `kill -9` the Addison shell
  itself (`signal` was granted unfiltered); `run_command` blocked the entire IPC pump
  for up to 30s; a `setsid()` grandchild could wedge the shell indefinitely; and an
  ancestor write-root could `mv` the recovery floor out from under its own deny.
- **The keychain dialog storm is fixed at its source.** Presence left the keychain for
  `provider_config.secret_presence`, so a polled question no longer touches the OS,
  and a foreign item now self-heals by delete-then-add. Verified against the owner's
  real keychain: the item's creation date moved to today and the key survived.
- **The gates and the docs became executable.** `scripts/gates.sh` is the one gate
  list; `tests/doc_claims.py` is a registry of load-bearing facts, one row each, with
  a test that names the file and line of any document contradicting one.

## Branch and PR state (verified 2026-08-08)

**No PR open. `master` carries everything through #82**: step 8's four phases and
their review (#65–#70), the review surface's three prerequisites (#71, #72, #74)
plus the picker read ceiling beside them (#73), its docs-first wave (#75) and all
five of its Build sections (#76–#78), the BUILD-LOG entry for that wave (#79), and
three gap closures after it (#80–#82). Work from `master`.
**Re-read this section
immediately after any merge:** it was
false for ninety minutes on 2026-07-26 because a merge falsified six passages without
touching the file that contained them, and no gate catches that.

**A stacked PR does NOT auto-retarget when you delete its base branch. It
CLOSES.** #66 was based on #65's branch; deleting that branch after #65 merged
closed #66 outright, and recovering it meant pushing the branch back at its old
commit, reopening, retargeting, and merging. Nothing was lost, and the order that
avoids the whole detour is: **retarget the child to `master` first, merge it, then
delete branches.** (GitHub's auto-retarget only fires while the base still exists.)

- Every `claude/*` branch left in the clone is fully contained in `master` and safe
  to delete: `bespoke-widgets-feasibility-72d532`, `mcp-phase-2-connect-discovery`,
  `mcp-phase-3-dispatch`, `mcp-phase-4-output-handling`,
  `model-switching-menu-ui-7f1c9c` (#60–#63) and `review-fixes-2026-08-07` (#64).
  Step 8's two branches were deleted when they merged.
- `archive/thread-window-wip` and `archive/icon-gen-wip` are parked worktree
  experiments, kept only so the attempts are recoverable. Neither is for merge.

## Three commits on `master` are red, and it is not what you think

**`607c9ec` fails one vitest case** (`parseWidgetList > carries the unavailable
marker through`): the test was staged into the pyright/eslint commit while the
implementation it exercises lands in `562bb6e`.

**`22c8876` and `6690fd2` fail `test_every_markdown_link_resolves`**: both link to
`secrets-and-keychain-plan.md`, which is not committed until `62d93a7`.

No code is wrong at any of the three, and the tip is green. **If you `git bisect`
across that range, expect them to fail for unrelated reasons**; `--skip` them.

The lesson is not the ordering, which is obvious once seen. It is that `607c9ec` **was
verified in isolation**, but only its Python half, and the result was then reported
as "verified green in isolation". A partial check described as a complete one is the
failure. Verify an intermediate commit against the whole of `ci.yml`.

## Six traps this session hit, all the same shape

Worth a minute before you write a test here. Each cost real time and each looked green.

1. **A deadline test that asserts output proves nothing about the deadline.** Assert
   the clock.
2. **A negative test passes when the mechanism never ran.** Every negative sandbox
   test now writes a marker in the same command and asserts the marker landed.
3. **Purifying a function for testability moves the untested part to its caller.**
   This has now happened four times: `seatbelt_profile`, the IPC pump's
   `dispatch_off_loop`, the bundle lookup in `addison_data_dirs`, and a source-pin I
   wrote that matched the word `dispatch_off_loop` **inside a comment** the mutation
   left behind. Where the last link cannot be reached at runtime, pin it at the
   source, and match the CALL, never the word.
4. **A normalizer whose every consumer is tested against hand-built fixtures.**
   Deleting `normalizeRailRoutines`'s only real line left all 417 tests green. Worth
   hunting elsewhere.
5. **A test that asserts by RAISING through code whose job is to swallow.** Every
   honest presence caller wraps its probe in `except Exception`, so an
   `AssertionError` was eaten and the test could never fail. Count instead.
6. **A guard the tests never exercise because the fixture cannot reach it.** The
   `STATEFUL_KINDS` gate: a timer-shaped state walked through the timer arm for a
   *routine* spec, because `0 > spec.get("seconds", 0)` is false.

The habit that catches all six: **mutate the line you think matters and confirm a
NAMED test dies.** It has now been wrong six times in this repo, and twice the tell
was that a mutation which *should* have killed something did not.

## Where the project stands

- v1 (spec §11, steps 1–11) and **all eight Phase-2 steps** are implemented and
  merged, and so is Phase 3's Developer review surface. What is left of Phase 3 is
  the packaging track. `ROADMAP.md` owns status.
- Addison is a **butler**: Developer = a Claude-Code-class coding harness; Simple = an
  all-in-one companion; Custom tunes prompting guards. Safety means **guaranteed
  rollback**, and that has code and tests behind it in both modes.
- **The dark v4 UI is on `master`.** `docs/design-brief-fern/` is history only.
- **Counts are deliberately not written down here.** They went stale twice in one
  day, and a stale number reads as a claim. `scripts/gates.sh` prints the real ones.
- CI runs the same three jobs on every push. Keep it green, and when a gate itself
  changes, wait for the first CI run afterwards before calling it done. That run *is*
  part of the change; twice on 2026-08-06 it was not treated as one.
