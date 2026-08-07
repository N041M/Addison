# Addison — session handoff

**Where things stand right now, and what to pick up.** Nothing durable lives here —
this file is expected to go stale and be rewritten. Everything that should outlive a
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
  weeks because TypeScript found `@types/node` in `/Users/karel/` — above the project
  entirely. A gate green for a reason that is not in the repository is worse than a
  gate that is red.

## Next up

**One step remains — 8, and phases 1–2 of four are BUILT.** `ROADMAP.md` owns
status; trust it over this file.

- **8 — the automation keyword gate. The plan exists; phases 1 and 2 landed
  2026-08-07** ([`step-8-automation-plan.md`](step-8-automation-plan.md) owns the
  phases and the decisions). Syntax was decided by the owner (a per-automation
  nonce Addison shows and you retype, because a fixed prefix is forgeable by
  anything that can write English); the plan settles what it gates (ARMING only),
  what automation IS in v1 (launchd user agents, macOS-only arming, typed shell
  surface that builds its own plist), and what a restore may never do (re-arm —
  no armed column exists, structurally). **Phase 1 = the fence + the inert
  table** (the eleven `OS_AUTOMATION_DIRS` un-trustable/denylisted/write-denied,
  lockstep-tested across Python and Rust). **Phase 2 = authoring**:
  `create_automation` (dev-only, MEDIUM, real undo, registered `open_only` so the
  undo check stays ENFORCED) with a four-refusal door — schedule bounds, the
  dispatch denylist asked at authoring (a draft whose command is `crontab` is
  refused as arming), secret shapes via the redactor, ASCII-folded unique labels
  — a chat-only plist preview (`plist_text` may never cross IPC; a source test
  pins the rpc layer cannot import it), `scheduleSentence` on the wire, and the
  Developer-only Settings drafts section. **Next: phase 3 — the nonce card +
  arming through a typed shell surface (`automation.install/remove/status`,
  shell builds its own XML, label prefix enforced, `RunAtLoad` never set), then
  phase 4 (state honesty + Simple's disabled rows + the restore-refresh hook
  phase 2 left owed, listed in the plan's phase-4 entry).** Read the plan's §3
  (nonce mechanics) and §7 (everything that flips in phase 3's commit — G2
  wording, flow 12, two not-armed copy lines, `primary.txt`'s scheduling
  sentence) before starting phase 3.
- **7 — MCP client. DONE FOR v1: phases 1–4 of five, 2026-08-06 to 2026-08-07.**
  Transport was decided by the owner on 2026-08-06: **HTTP only for v1**, which is
  what keeps the client in the Agent Core and adds no new highest-trust surface.
  Configuration, then connect + discovery (`agent_core/mcp_client.py` speaks the
  protocol, `agent_core/mcp_catalog.py` admits what it finds to the ONE registry
  namespaced and dev-only), then dispatch through the ordinary gate as HIGH and
  destructive with `tool_audit` on every outcome, then output handling. **Phase 5 is
  a recorded later option, not a missing piece** — stdio under containment, and SAFE
  admission via a promoted allowlist. If you are picking this subsystem up, read the
  plan's §4.2 scoping decisions first (no auth, on-demand only, catalog in memory):
  each is a thing the next phase may want and must decide again rather than inherit.
  Plan: [`step-7-mcp-plan.md`](step-7-mcp-plan.md).

**The keychain thread is half done.** Plan steps 1–2 and §5.2/§5.3 shipped; steps 3–5
(`Intent`, launch reconciliation, the shipped read counter, the cards) have not.
Read [`secrets-and-keychain-plan.md`](secrets-and-keychain-plan.md) before touching
`keychain.rs`. **`FAILED_READS` survives on purpose** — its presence role is gone, but
two background callers still fetch key *values*, and deleting it now would let a
launch task re-raise a dialog somebody had dismissed.

**Two queued, contract-first, not started:** rework local-model setup (state-aware —
not-downloaded → one-click download plus a source link; downloaded → how to connect
it; and more open-source models), and skills file-upload (an uploaded text file's
contents become the skill's guidance text — editable, previewed, size-limited).

## What changed on 2026-08-07, in one paragraph each

Four PRs merged (#60–#63), then a review of all four found about twenty-five real
defects and they were fixed the same day, and then the step-8 plan was written and
its phase 1 built. `BUILD-LOG.md` owns the findings; these are
the ones that change how you should read the tree.

- **Phases 1–2 were then reviewed, and the review's own fixes were reviewed
  again.** Four read-only reviewers over disjoint scopes, then an adversarial pass
  over the fixes — which found three regressions the fix round had introduced, one
  wider than the defect it fixed. `BUILD-LOG.md` owns the findings. The two worth
  knowing before you touch this subsystem: **`plist_text` had no real test at all**
  (its only assertion compared the function against itself, so dropping its XML
  escaping passed 1449 tests), and **the arming fence's blast radius is not what a
  comment says it is** — a step-over meant for `VAR=value` chained through any
  `=`-bearing word until it was caught and narrowed.
- **Step 8 has a plan and landed phases 1 and 2** — see "Next up" above for the
  whole shape. What changes how you read the tree: `~/Library`, `~/.config` and
  the eleven OS-automation directories are no longer trustable workspaces,
  commands naming them (or invoking `launchctl`/`crontab`/`at`/`batch` as a first
  word) are refused pre-gate with their own sentence, `policy.trust_refusal` is
  the reason-reporting form of `workspace_trust_allows`, and the `automations`
  table now fills ONLY through `create_automation` (dev-only, four-refusal door)
  — still no armed column and no way to arm, by design until phase 3.

- **Step 7 is COMPLETE for v1.** Phases 2, 3 and 4 all landed: a tool server's tools
  are discovered, callable through the ordinary gate, and what one answers is
  redacted, bounded and disclosed rather than filtered. Simple sees none of it.
- **The two model pickers are a folder tree** — company, then family, then model, one
  folder open at a time, drawn by the composer menu and the Settings popup from one
  engine (`shell/src/lib/modelGroups.ts`) so they cannot disagree. Owner decision.
- **The review's two biggest finds are worth knowing before you touch either file.**
  The `tool_audit` rebuild could strand every audit row permanently if it was
  interrupted; and the structured channel's redaction ran after serialization, which
  turned it off for any credential a control character had split — and made the audit
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
  in Simple as disabled rows that say why, instead of vanishing — that changed a
  documented rule, and `SAFETY.md` owns the new one. Simple also gained three
  interactive widget kinds (checklist, note, timer), with what you have *done* with a
  widget kept in a separate `widget_state` table from what the widget *is*, excluded
  from snapshots so a restore never unticks your list. The capability-declaration
  lattice was **cut, not deferred**: the closed kind list is the same gate with
  nothing to get out of step.
- **The audit trail was leaking secrets.** `tool_audit.detail` stored the raw command
  text, so an exported key landed verbatim in the one table excluded from snapshots
  and never pruned. Redacted at the single write, and the test that was supposed to
  catch it was vacuous — its tool defined no `permission_detail`.
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

## Branch and PR state (verified 2026-08-07, end of day)

**No open pull requests. `master` carries everything through #66** — step 8's plan,
phase 1 (the fence and the table), phase 2 (authoring), and the review of both,
merged 2026-08-07 with CI green on the merge commit. Work from `master`.
**Re-read this section
immediately after any merge:** it was
false for ninety minutes on 2026-07-26 because a merge falsified six passages without
touching the file that contained them, and no gate catches that.

**A stacked PR does NOT auto-retarget when you delete its base branch — it
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
across that range, expect them to fail for unrelated reasons** — `--skip` them.

The lesson is not the ordering, which is obvious once seen. It is that `607c9ec` **was
verified in isolation** — but only its Python half, and the result was then reported
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
   source — and match the CALL, never the word.
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
NAMED test dies.** It has now been wrong six times in this repo — and twice the tell
was that a mutation which *should* have killed something did not.

## Where the project stands

- v1 (spec §11, steps 1–11) and Phase-2 steps 1–7 are implemented and merged. **8 is
  not started.**
- Addison is a **butler**: Developer = a Claude-Code-class coding harness; Simple = an
  all-in-one companion; Custom tunes prompting guards. Safety means **guaranteed
  rollback**, and that has code and tests behind it in both modes.
- **The dark v4 UI is on `master`.** `docs/design-brief-fern/` is history only.
- **Counts are deliberately not written down here** — they went stale twice in one
  day, and a stale number reads as a claim. `scripts/gates.sh` prints the real ones.
- CI runs the same three jobs on every push. Keep it green — and when a gate itself
  changes, wait for the first CI run afterwards before calling it done. That run *is*
  part of the change; twice on 2026-08-06 it was not treated as one.
