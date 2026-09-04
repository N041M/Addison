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

**`docs/plans/` is not in this repository.** Twelve design documents — every
`*-plan.md` — were bundled there and gitignored on 2026-08-24 (owner decision). If
your clone does not have the folder, links into it from `CLAUDE.md`, `ROADMAP.md`,
`SAFETY.md`, `KNOWN-GAPS.md` and the source comments will not open, and ten rows in
`tests/doc_claims.py` name an owner document you cannot read. The gates stay green
either way — [`README.md`](README.md)'s Plans section says exactly what that green
does and does not cover, and `BUNDLED_PLANS` in `tests/test_docs_drift.py` lists
what is missing. Ask the owner for the folder before doing design work.


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

**START HERE: two pull requests are open and green, and the owner merges them in
order.** [PR #156](https://github.com/N041M/Addison/pull/156) (the permission card's
command as a field, H9) first, then [PR #157](https://github.com/N041M/Addison/pull/157) (Knowledge phase 3, which carries
the day's build-log entry for both). Both passed every CI job including the two
Windows runners; both went through an adversarial review, a fix round and a
regression pass over the fixes. After the merge, fast-forward the main checkout at
`/Users/karel/Desktop/Addison` (it serves `tauri dev`) and delete the three
`claude/knowledge-phase-3*` and `claude/permission-card-command` worktrees under
`.claude/worktrees/`.

**Then the manual passes, which need a person at the keyboard:**

1. **The real-Ollama pass for Knowledge** — nothing in three phases has spoken to a
   real embedding endpoint. Ollama is NOT installed on this Mac (`which ollama` finds
   nothing); it needs `brew install ollama` and `ollama pull nomic-embed-text`, then
   [`TESTING-CHECKLIST.md`](TESTING-CHECKLIST.md) §17 end to end, in Simple AND
   Developer, on a proven-fresh bundle (the webview-cache fossil trap below applies:
   clear BOTH cache directories and prove the build from inside the page first).
2. **The real-Telegram pass** for messaging channels (unchanged from 2026-08-22; the
   owner's own bot token, pasted by the owner).
3. **The review surface's §13c pass** (unchanged).

**Then the queue behind them, in the order that pays best:**

1. **One owner decision the day surfaced, cheap once decided: H14 in the
   test-hardening plan.** `open_link` validates only the scheme while `read_web_page`
   vets addresses through `net_vetting`, so injected page text can steer Addison to
   open a router admin URL in the person's real browser. The mechanism is one call;
   the decision is whether a LAN or loopback link is refused outright, carded per
   invocation, or allowed — a developer opening `localhost:3000` is the case that
   makes it a decision.
2. **The image-attach train** ([#148](https://github.com/N041M/Addison/pull/148)
   → #149 → #150 → #151 → #152, built 2026-08-23, all still open). #148 conflicts
   with master and commits its plan at `docs/image-attach-plan.md`, which the
   2026-08-24 decision moved into the gitignored bundle. The owner's calls first:
   close [#147](https://github.com/N041M/Addison/pull/147) as superseded (carry its
   document-input scope forward later) and close
   [#130](https://github.com/N041M/Addison/pull/130), whose strikes master already
   carries. Then rebase the train, relocate the plan, add its `BUNDLED_PLANS` row.
3. **The menu-bar popup chat window** (approved in direction only; needs its design
   section in `messaging-channel-plan.md` and the owner's yes on specifics).
4. **Phase 3's packaging track** (signing, notarisation, `updater.rs`).
5. **The parked owner decisions in KNOWN-GAPS** (explicit-pick-vs-Cost-first
   precedence, the `open -a Addison` question, the Custom workspace-trust guard, the
   `revertable` tri-state).
6. **The judged feature queue**: per-task model assignment
   (`model-assignments-plan.md`, proposed), then notes-as-attachment. Knowledge has
   left this list.

## What changed on 2026-09-04, in one paragraph each

`BUILD-LOG.md` owns the findings (one entry, "What shipped 09-04", for both pieces of
work). These are the ones that change how you read the tree.

- **Knowledge phase 3 landed: the Settings section "Your documents", in every
  profile.** `rpc/knowledge.py` (`knowledge.list/add/reindex/remove`), one new shell
  method `shell.pickKnowledgeDocument` (text and Markdown, 2 MB, UTF-8, the data-dir
  floor, sha256 of the bytes read) and one new digest method
  `shell.digestKnowledgeDocuments` with its own bound. **The design rule: the shell
  never reads a document's bytes for the core without a picker in between** — Update
  and Try again re-open the picker on the file. Add runs worker → thread → worker
  (`knowledge_commit`); the thread touches no store. Nothing has spoken to real Ollama.
- **Four native dialogs came off the shell's stdout pump.** `dispatch_dialog_off_loop`
  in `agent_process.rs` spawns `shell.pickFile`, `shell.pickDirectory`,
  `shell.pickKnowledgeDocument` and `shell.saveNewFile` off the reader loop. Before today, every Core→Frontend
  frame stalled while a picker stood open, and a Core→Shell request made from the
  worker meanwhile died at the bridge's sixty-second ceiling.
- **The permission card's command is a field** (PR #156): `permission.requestGrant`
  carries `command`; `description` is the lead sentence; `PermissionCard.tsx` parses
  nothing. **Found in passing: `normalizePermission` in `App.tsx` had never copied
  `preview`, so the delete preview (2026-08-13) and the routine-sharing taint line
  (2026-08-15) never reached a card in the running app.** Fixed there; KNOWN-BUGS #16.
- **A claim row now guards Knowledge's status** (`knowledge-is-built` in
  `tests/doc_claims.py`): the docs map, KNOWN-GAPS and HANDOFF all said "nothing
  built" or "phase 1 only" for eleven days after phases 1–2 merged, and no row saw it.

## Traps found on 2026-09-04, worth a minute before mutation-testing anything

- **Restore-within-a-second leaves a poisoned `.pyc`.** CPython validates bytecode
  against the source mtime in WHOLE SECONDS, so mutate → run → restore inside one
  second makes the next run execute the mutant from `__pycache__` while the source is
  byte-identical to HEAD. Two reviewers lost time to it today. Purge
  `agent_core/**/__pycache__` after every restore and re-run the baseline before
  believing a red.
- **The `.gitignore` rule `node_modules/` matches directories, not symlinks.** A
  worktree's `shell/node_modules` symlink to the main checkout's install gets swept up
  by `git add -A`. Add files by name in worktrees, or `git rm --cached` it before
  merging (it happened once today and was caught at the merge).
- **`pyrightconfig.json`'s `venvPath` is relative, so pyright in a worktree reports
  phantom missing imports.** Pass `--pythonpath /Users/karel/Desktop/Addison/agent_core/.venv/bin/python`.
  Real result today: 0 errors on both branches.
- **A fix is new code, sixth instance.** Sending `command` as a field made the expired
  arming card draw the automation's NAME in the command block (its per-call detail is a
  name). The regression pass over the fixes is not optional.
- **Two size bounds can each be right and jointly wrong.** The picker admitted 2 MB;
  the digest answered "cannot tell" above 256 KB; nothing related them, so "changed on
  disk" was unreachable for most documents the feature exists for. When a new surface
  reuses an old bound, read the comment that justifies the number.

## Branch and PR state (verified 2026-09-04)

- **Open, green, awaiting the owner:** [PR #156](https://github.com/N041M/Addison/pull/156)
  `claude/permission-card-command` and [PR #157](https://github.com/N041M/Addison/pull/157) `claude/knowledge-phase-3`
  (which absorbed `claude/knowledge-phase-3-ui`; the UI branch is not pushed and can be
  deleted). Merge #156 first.
- **Open and stale, the owner's to resolve:** #147, #148–#152 (the image-attach
  train; #148 conflicts with master), #130 (superseded).
- **Worktrees under `.claude/worktrees/`:** `knowledge-phase-3`, `knowledge-phase-3-ui`,
  `permission-card-command` are today's and can go after the merge; the three
  detached ones from August (`app-development-*`, `gracious-villani-*`,
  `wonderful-shannon-*`) are older sessions' and were left alone.
- The `archive/*` branches are named history and stay. **The main checkout is still
  at #155's merge (`21ff450`)** — fast-forward it after merging.

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

## Six traps the 2026-08-08 session hit, all the same shape

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

- v1 (spec §11, steps 1–11), **all eight Phase-2 steps**, Phase 3's Developer
  review surface, **messaging channels phases 1–3**, the Windows port's phase 1 and
  **all three phases of Knowledge** (phase 3 in the open PR) are implemented. What is
  left of Phase 3 is the packaging track. The channels' phase 4 is deferred toward a
  bespoke phone app. `ROADMAP.md` owns status.
- Addison is a **butler**: Developer = a Claude-Code-class coding harness; Simple
  = an all-in-one companion; Custom tunes prompting guards — and since
  2026-08-22 a paired phone can converse with it and use a three-tool read-only
  floor that is provably a subset of what Simple sees. Safety means **guaranteed
  rollback**, and that has code and tests behind it in both modes; a restore
  stops every channel listener and never re-pairs a revoked phone.
- **The dark v4 UI is on `master`.** `docs/design-brief-fern/` is history only.
- **Counts are deliberately not written down here.** They went stale twice in one
  day, and a stale number reads as a claim. `scripts/gates.sh` prints the real
  ones.
- CI runs the same three jobs on every push. Keep it green, and when a gate
  itself changes, wait for the first CI run afterwards before calling it done.
  That run *is* part of the change; twice on 2026-08-06 it was not treated as
  one.
