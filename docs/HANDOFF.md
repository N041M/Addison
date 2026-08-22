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

**START HERE: the manual real-Telegram pass.** Messaging channels phases 1–3 are
BUILT and merged (2026-08-22, PRs #143–#145;
[`messaging-channel-plan.md`](messaging-channel-plan.md) owns the design and the
eleven answered owner decisions), and **nothing in the tree has ever spoken to
real Telegram** — every test runs against `httpx.MockTransport` and Telegram's
published limits. The pass needs the OWNER'S hands for one step: a bot token from
@BotFather, pasted into Settings → "Your phone" by the owner themselves (it is a
credential; the assistant never touches it). Then, in order: connect ("Check
now" shows the bot's name), enable, pair a phone with the desktop-shown code, and
check (a) a lookup and a calculation answer from the floor, (b) *"add a line to my
notes file"* comes back with the full refusal sentence AND the note appears under
the panel's pending block without a manual refresh (the `channel.requestQueued`
frame), (c) "Ask this here" lands the sentence in the desktop composer and the
card appears only after Send, (d) Dismiss clears it, (e) the `on_wake` setting
both ways (default declines a stale message; 'answer' answers it late), (f) the
queue is empty after a restart. **Before believing anything on a live screen,
prove the build from inside the page** — the webview-cache fossil trap below.

**Then the queue behind it, in the order that pays best:**

1. **The menu-bar popup chat window** (approved owner scope, 2026-08-22 decision
   4): background operation plus a small popup chat on a menu-bar item. It is
   approved in DIRECTION only — the plan's own rule is that it gets its own design
   section in `messaging-channel-plan.md` before anything builds it (a resident
   process and a second chat surface each have their own trust story). Write the
   section, get the owner's yes on its specifics, then build.
2. **The review surface's §13c manual pass** (`TESTING-CHECKLIST.md`), still live
   and unrun — the widened CSP is enforced by a real webview and by nothing else.
   The bright line if it fails: do not widen `script-src` or admit `blob:`.
3. **Phase 3's packaging track**: signing, notarisation, the auto updater
   (`updater.rs` is a nine-line stub and the tree's only `TODO(step N)`),
   previous-binary restore, Secure Enclave identity.
4. **Parked owner decisions** (KNOWN-GAPS): the explicit-pick-vs-Cost-first
   precedence rule (the UI half shipped 2026-08-22 as the composer's "Answered
   by" line; the rule itself is still open), the `open -a Addison` automation
   question, the Custom workspace-trust guard question, the `revertable`
   tri-state wire change.
5. **The judged feature queue**: Knowledge/retrieval is next (its screening
   prerequisite is met; the clean shape is recorded in KNOWN-GAPS), then per-task
   model assignment (`model-assignments-plan.md`, proposed), then
   notes-as-attachment. **Phase 4 of the channels plan (approving actions from a
   phone) is DEFERRED, not queued** — the owner's horizon for that is a bespoke
   phone app, which is not designed anywhere yet.

## What changed on 2026-08-22, in one paragraph each

One session, eleven PRs (#136–#145 plus the plan's #141/#142), all merged same
day at the owner's direction. `BUILD-LOG.md` owns the findings (five entries for
the day, "(second)" through "(fifth)" plus the channels entries); these are the
ones that change how you read the tree.

- **The progressive-markdown streaming was reworked** (#136): every frame is now a
  fresh parse of the true prefix cut at the last newline behind the scramble's
  resolved edge; no frozen boundaries, no never-the-last-node rule, and the fence
  machinery (`fenceEndOffset`/`tailIsFence`) is deleted. Blocks are keyed by
  content hash. Verified LIVE on a proven-fresh bundle: a table ending an answer
  renders from its header and grows row by row.
- **Three thread features landed** (#137–#139): the composer's "Answered by"
  disclosure (derived from the thread, never stashed — staleness across a
  conversation switch is unrepresentable), Highlight → Ask/Explain (a selection
  popover seeding the composer with a blockquote; `SelectionAsk.tsx`), and
  truncation-aware Continue — which found **three provider adapters erasing the
  stop reason** (google never read `finishReason`, ollama never read
  `done_reason`, openai's non-streaming path collapsed it). Cap spellings now
  live on `ProviderCapabilities.truncation_finish_reasons`, membership-tested by
  the orchestrator with no literal anywhere.
- **Messaging channels went from nothing to built in one day**: plan written
  (#141), all eleven owner decisions answered and recorded in the plan's §5
  (#142), then phases 1–3 (#143–#145). What changes how you read the tree:
  `channels.enabled` is saved INTENT and `ChannelService` is the truth — nothing
  starts a poll loop at launch, and every surface reads live state (step 8's
  lesson); `rpc/channels.py` carries a deliberate import fence (no httpx/
  threading/tools imports, AST-tested); the poll loop is a reviewed entry in
  `test_g2_no_self_trigger.py`'s `_REVIEWED_THREAD_TARGETS`; the remote floor is
  a closed three-id set proven a SUBSET of `visible_tools(SAFE)` at two test
  sites plus a `doc_claims` row; and `PendingRequest` carries no tool id or
  arguments — the dataclass shape IS the no-replay guarantee.
- **The step-1 deferral ledger, for CLAUDE.md's pointer**: the only still-open
  item is `tool_grants` capture — excluded from snapshots because restoring a
  grant revoked after the snapshot would reinstate a privilege through the
  deliberately ungated one-action restore; if ever captured it must be an
  INTERSECT, never a replace. Everything else from that ledger landed and is
  named in CLAUDE.md itself.

## Traps found on 2026-08-22, worth a minute before live-verifying anything

- **The fossil trap has a SECOND DOOR: the webview's own cache.** A freshly built,
  freshly launched debug bundle (old bundle deleted, process path verified) still
  served a stale `index-*.js` out of `~/Library/WebKit/app.addison.desktop` and
  `~/Library/Caches/app.addison.desktop` — a script existing nowhere on disk
  outside the cache — faking "feature missing" for a whole merged wave. Clear
  both cache directories before a live pass, and prove the build from INSIDE the
  page: `Array.from(document.scripts).map(s => s.src)` in the inspector must
  match the hash in `shell/dist/index.html`. The BUILD-LOG's 08-22 fossil entry
  owns the full story.
- **Commit BEFORE mutation-testing.** Restoring a mutated file with
  `git checkout -- <file>` restores HEAD — which, on uncommitted work, wipes the
  work. It happened once and was recovered only because the file had been read
  into context in full.
- **A spy tool's NAME can silently invalidate a test.** An orchestrator pin used a
  spy named `calculator`; when the remote floor later admitted that id, the test
  stayed green while its asserted sentence ("a remote turn may not reach a tool
  at all") went false. When a closed set changes, grep the test fixtures for the
  ids it now contains.
- **`httpx` exception strings carry the URL, and some APIs put credentials in the
  URL.** Telegram's bot API does. Every raise in an adapter names a frozen
  constant, no `from exc` chaining, and a test asserts the token reaches no
  request body, row, payload or database byte. Any future adapter must keep this.

## Branch and PR state (verified 2026-08-22)

**No PR open; no feature branches remain. `master` carries everything through
#145.** The four feature PRs #136–#139, the docs PRs #140–#142, and the channel
phases #143–#145 were merged sequentially with conflicts resolved by rebase (the
same-day BUILD-LOG entries are stacked "(second)" through "(fifth)" per the
file's convention). One older PR was left alone deliberately: **#130**
(KNOWN-BUGS doc strikes, from an earlier session) — the owner's to merge or
close; its branch `claude/strike-known-bugs` is checked out in another worktree.
The `archive/*` branches are named history and stay. **The main checkout at
`/Users/karel/Desktop/Addison` serves `tauri dev` and was fast-forwarded to
#145's merge** — after any worktree-side merge, pull it forward or the owner
watches stale code (the 08-22 BUILD-LOG entry records the hour that costs).


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
  review surface, and now **messaging channels phases 1–3** are implemented and
  merged. What is left of Phase 3 is the packaging track. The channels' phase 4
  is deferred toward a bespoke phone app. `ROADMAP.md` owns status.
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
