# Addison — Session Handoff (2026-07-26)

For the next working session. Read **CLAUDE.md** first (repo law), then
**`docs/addison-scope-amendment-2026-07.md`** (the adopted scope shift — it
governs where it and the older specs disagree, *except* where an inline
owner-decision note supersedes it), then this.

**Next up: Phase-2 step 5.5 (containment for the OPEN harness —
[`docs/step-5.5-containment-plan.md`](step-5.5-containment-plan.md), proposed
2026-07-26), then 6 (widget capability tiers), 7 (MCP client) and 8 (the automation
keyword gate).** 5.5 is step 5's unfinished half rather than new scope, and it is
listed first because **G3 is currently overclaimed in OPEN mode** — see its entry
under "Next up". Step 7 is downstream of 5.5's audit log. **Phase-2 steps
1–5 are all BUILT and merged** — step 1 (the snapshot/rollback floor, G3, PR #47,
plus the ledger retirement in #51), step 2 (Custom profile + guards + the G4
anchor caller, #52), step 3 (routing strategies, #54), and steps 4 + 5
(free-model endpoints and the coding harness + workspace-trust, #56). Each has a
"What shipped" section below, **and each one's post-build rigor pass is where the
real defects were — read those before trusting any of it.**

**Two queued, contract-first, not started:** rework local-model setup
(state-aware — not-downloaded → one-click download plus a source link;
downloaded → how to connect it; and more open-source models), and skills
file-upload (an uploaded text file's contents become the skill's guidance text —
editable, previewed, size-limited).

**Branch and PR state (re-verified 2026-07-26, after PR #58 merged).** **There
are no open pull requests.** **`master` is now the branch that carries
everything** — work from it, not from `redesign/dark-v2`.

- **PR #58 is merged** (`a22badd`, 2026-07-26): the dark v4 redesign, the
  adversarial-review fix wave, the docs passes, the brand/icon fixes **and thread
  windowing**, all at once. `redesign/dark-v2` is still pushed but is now fully
  contained in `master` and safe to delete.
- **Four commits landed after that merge** — the brand icon-pipeline fix
  (`cc70ea8`), the pointer-glow revert (`e98828c`), the widget rail / model-menu
  fixes (`3ab1159`) and the starfield removal (`07cc9ee`). See "What shipped
  07-26 — after PR #58" below.
- **Then per-token streaming** (`d2174c1`, `0d6eec6`) and the branch-state
  correction (`f4ad86a`), pushed direct to `master` — see "What shipped 07-26 —
  per-token streaming". This doc pass sits on top of those.
- **`windowed-thread` no longer exists**, locally or on the remote. Its one
  commit was rewritten as `839bcff` and merged with #58. **Thread windowing IS
  shipped** — an earlier version of this file said the opposite in three places.

`archive/thread-window-wip` (the windowing experiment plus its scratch harness)
and `archive/icon-gen-wip` (an alternative icon-generation approach) are parked
work from worktrees, kept only so the attempts are recoverable. Neither is
intended for merge.

**How this section went stale, since the same trap is still open.** Everything
above was written by `7444c8e` ("bring every document back in line with the
tree") *while the redesign was still unmerged* — accurate at the time, false
about ninety minutes later when #58 merged, because a merge changes what a
branch-state paragraph asserts without touching the file that asserts it. Nothing
in the gates catches that. When you merge, re-read this section and "Where the
project stands" before anything else.

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

## Where the project stands

- The whole v1 build order (engineering-spec §11, steps 1–11) is implemented and
  **merged to `master`**. No open PRs, no stacked chain — the stacked-PR era is
  over; every change now goes PR → `master` directly.
- **A major scope amendment was adopted 2026-07-20.** Phase 1 (docs) landed, and
  **Phase-2 steps 1–5 are all built and merged** (G3 snapshots; Custom profile +
  guards + the G4 anchor; routing strategies; free-model endpoints; the coding
  harness + workspace-trust). Addison is a **butler**: Developer = a
  Claude-Code-class **coding harness**; Simple = an all-in-one **companion**; a
  **Custom** profile tunes prompting guards. Safety is redefined as **guaranteed
  rollback**, and that redefinition has code and tests behind it. **Steps 6, 7
  and 8 are not started.**
- **The dark v4 UI is on `master`** (PR #58). The Fern direction is gone from the
  tree, not merely superseded — the alias blocks, the serif family, the bundled
  fonts and the bell mark were all removed in the 4/4 cleanup. Anything this
  document says about the app's appearance describes `master`.
  `docs/design-brief-fern/` is kept as history only.
- **Gates, all green** on `master`: pytest, **pyright 0 errors**
  (repo-wide; the remaining diagnostics are `reportMissingImports` for
  `pytest`/`httpx`, pre-existing — pyright has no venv), ruff clean, vitest,
  ESLint clean, `tsc --noEmit` + `vite build` clean, Rust `cargo test` clean.
  **Exact counts are deliberately not written down here** — they went stale twice
  in one day and a stale number reads as a claim. Run the gates; the commands are
  under "Environment facts". **Green gates are not the bar — see "How step 1 was
  verified" below.**
- **CI exists** — `.github/workflows/ci.yml`, three jobs (python:
  ruff·pyright·pytest / frontend: eslint·tsc·vitest·build / rust: cargo test) on
  every PR and push to `master`. Keep it green.

## What shipped 07-20 — Phase-2 step 1, the G3 rollback floor

The floor everything else leans on. The motivating story is worth re-reading in
amendment §1 before touching any of it: a non-technical user asked his AI tool to
"make the models run as cheaply as possible", it broke his setup permanently, and
the built-in rewind did not fire. **The one requirement that outranks every other
line in that subsystem is "restore always works, even from a broken config."** The
test of that name is in `tests/test_snapshots.py` and heads the file on purpose.

**Storage.** `config_snapshots` in `agent_core/memory/schema.sql` — 12 columns,
two indexes, and **two `RAISE(ABORT)` triggers** that refuse to delete an
`undeletable = 1` row and refuse to clear the flag. Permanence lives in the
database, not in a `WHERE` clause a future query can forget. `ConfigSnapshot` /
`RestoreResult` in `agent_core/snapshots/model.py` mirror it 1:1.

**Capture scope.** `agent_core/snapshots/scope.py` — a declared table set *and* a
declared column set, both with completeness tests. Adding a Phase-2 table or a
column to a captured table turns the build red until you decide, in code, whether
it is captured or excluded. That is not pedantry: restore is replace-all with an
explicit column list, so an uncaptured new column would be silently reset to its
default **by the recovery path** — a restore would wipe the routing strategy or
the Custom guard toggles you are about to add.

**The manager.** `agent_core/snapshots/snapshot_manager.py` (large, and roughly
half of it comment and docstring — that ratio is intentional here) —
`capture` / `mark_verified_working` / `restore` / `restore_last_working` /
`last_working_target` / `list` / `delete` / `mint_anchor` / `prune`, plus two
store-free module functions for disk recovery. It imports stdlib plus the two
schema-mirroring leaves and **nothing else** — no provider, router, profile,
policy mode, registry, or gate. Retention (50 rows / 30 days) and the payload
version are **module constants, not settings**, so nothing the model can write
shrinks the rollback window.

**Two writes, always.** Every payload goes into the row *and* into a `0600` JSON
sidecar at `<db_dir>/snapshots/<id>.json` (dir `0700`). That is the answer to "the
database itself is the broken thing": `snapshot.list` and
`snapshot.restoreLastWorking` are the only two RPC methods **exempt** from the
server's build-failure short-circuit, and with no usable Store they are served
from the sidecars. A restore on that path renames the damaged file **aside**
(`<db>.damaged-<epoch>` — never deletes it) and rebuilds, in the **same session**,
with no restart. Three tests cover it, including the byte content of the renamed
file. `snapshot.list` deliberately does *not* rename anything — a look must not
cost you your database.

**RPC + wiring.** Five `snapshot.*` methods, the `shell.appBuildRef` Core→Shell
call (Rust: `shell/src-tauri/src/app_build.rs`), `agent_core/rpc/snapshots.py`
(the sole snake→camel mapper), and six touch points in `main.py` — including
moving `_ensure_built()` inside the worker's error handling, which was a real hole:
a broken store used to hang the process forever.

**Hooks.** Seven auto-capture sites + one verified-working site, one line each,
with a deliberate **capture-failure policy split**: the four hooks whose old
content exists nowhere else (delete a routine / widget / note, change a note)
**refuse the change** if the snapshot cannot be taken; the three recoverable ones
proceed and raise a **sticky warning** that only a successful manual save clears.

**Frontend.** The Settings **"Restore points"** section (never called
"Snapshots" in any user-facing string), placed directly under Profile, plus the
Restore-points modal and the Snapshots surface that share its rows by value. The
restore action is **accent, never the rose `danger` token** — a recovery is not a
destruction — with a two-step inline confirm carrying the consequence copy plus a
profile-change sentence and a genesis sentence when they apply, the target always
named with its timestamp and **kept on screen through the confirm**, and the
blocky **Permanent** tag (small caps on a 2px accent rule) with no Remove control
on anchors. QA steps: **TESTING-CHECKLIST §13a**.

**Two decisions worth internalising before you extend it:**

- **Restore is an RPC path, never a registry tool, and never passes the permission
  gate.** A gate that could deny a restore would make "the restore path is itself
  unbreakable" false. The only model-facing snapshot surface that will ever exist
  is the **LOW, capture-only** `snapshot_now` tool — add a row, nothing else.
  (Shipped 2026-07-24; an AST source test holds it to `capture` alone.)
- **`created_in_mode` never filters a snapshot query, in any mode.** The
  engineering spec's DDL comment said the column "mirrors existing artifact
  hiding"; that was **overridden, not followed**, and both the spec and
  `data-model.md` now say so. Following it would hide the way back from exactly
  the user who most needs it: weakened a guard in Custom → broke something →
  switched to Simple → opens Restore points to an empty list. Two tests hold the
  line, one behavioural and one **source-level**
  (`test_no_snapshot_query_filters_on_created_in_mode`) that reads the SQL and
  fails on a filter position — because a behavioural test only proves today's
  behaviour, and would not stop someone adding `AND created_in_mode = ?` next
  quarter.

## What shipped 07-24: step 3 — routing strategies

Built from a contract that took TWO adversarial review rounds (round 1:
REDESIGN — the drafted quality-first silently overrode the user's standing
default model, the fallback trigger assumed an error classification the
providers don't have, and per-turn vs per-send was undefined; round 2 on the
redraft: AMEND-THEN-BUILD with five sharper fixes). **Owner decision: Balanced
is CUT from v1** — the drafted version was provably identical to cost-first at
two-model pools; amendment §10.1 carries the note.

- **Stage 0, load-bearing:** `ProviderUnavailable` / `ProviderRequestRejected`
  / `ProviderAuthFailed` in `providers/base.py`, raised by every provider from
  the existing collapse points with byte-identical messages. Fallback advances
  ONLY on Unavailable — Rejected/Auth fail the turn at once (the next provider
  would get the same bad request / same missing key). Providers also accept a
  per-call timeout override — the budget's teeth.
- **Chains** (`resolve_chain`, pure, store-free): the HEAD of every
  cloud-containing chain is the user's standing default (`selected_primary`) —
  strategy orders only the tail, so the freeze is structural and rank can
  never override a deliberate weaker-model choice. One resolution path:
  absent key ≡ quality_first (a dual path made the Simple toggle's round-trip
  observable). Unknown-rank models sort behind the head, never demoted. All
  Ollama candidates share `provider_id="ollama"`.
- **The attempt loop** (orchestrator): per-send continuation, never restart;
  cross-provider mid-turn advance FORBIDDEN in v1 (foreign tool_use history
  into another vendor's translator is unverified — the same-provider case, two
  Ollama models, is allowed); `_COOLDOWN_SECONDS=60` and
  `_FALLBACK_BUDGET_SECONDS=120` as module constants, the budget enforced as a
  REAL per-attempt deadline (`timeout=min(default, remaining)`) so a single
  hanging candidate cannot blow it — the test uses a genuinely BLOCKING mock,
  because an instant-fail mock cannot see this gap.
- **`local_only` outranks everything**: resolved BEFORE the Setup-Assistant
  relay branch (the relay is a cloud call), and an explicit per-message cloud
  pick under local_only is refused in plain words — the privacy invariant has
  no per-message bypass.
- **`answeredWith` + the chip**: `on_answered` carries (model, label, free,
  routed) with `routed ≡ answering ≠ explicit pick` — an explicit pick that
  fell forward to a free model DOES chip. `on_usage` now carries the RESOLVED
  per-attempt identity, **fixing a pre-existing bug**: `_usage_identity`
  attributed every routed turn to the catalog default.
- **`routing.*` RPC**: closed vocab, custom chain validated at set (unknown
  ids refused), hook split — a strategy change snapshots-and-proceeds
  (`routing_change`), a custom-chain OVERWRITE refuses if the snapshot fails
  (user-authored content, the note-overwrite policy). Simple sees the one
  toggle; Developer/Custom the full picker + chain builder.
- Verified: mutation-proven throughout (coordinator personally killed the
  freeze head, the budget threading, and the local_only interlock); an
  11-check live driver over real JSON-RPC including a real model turn carrying
  `answeredWith`.

**The step-3 post-build rigor pass (same day).** The adversarial hunt confirmed
the load-bearing invariants non-vacuous and found one real (low-severity) bug
plus three smaller items, all fixed and kill-verified:
- **The internal connect-retry could double the fallback budget**: a
  ConnectTimeout inside `request_with_retry` retried once BEFORE the
  orchestrator regained control, so one candidate could run ~2× its deadline.
  Now a caller-supplied deadline disables the internal retry — the chain IS the
  retry (`allow_retry=timeout is None`, all five providers); standalone calls
  keep today's robustness (both pinned).
- **The vanished-custom-chain-id note existed in the contract but not the
  code** — the skip shipped silently. Now one plain Activity note names the
  skipped models, tested over the wire.
- **A head cooled by a previous turn suppressed the fallback note** —
  `preferred` is now the pre-cooldown chain head, so quiet substitution is
  impossible.
- The contract-NAMED `test_local_only_never_reaches_the_relay` now exists
  (keyless Simple + relay redirect armed + interlock forces LOCAL; kill-verified
  loud).
- **Known gap, deliberate:** a Simple user whose stored strategy is
  `local_only`/`custom` (set in a Developer session) sees the two-option toggle
  with neither option active while the real strategy still governs — a
  migration/UX edge needing design, not a silent bug; the strategy is honest in
  `routing.get`. Decide the toggle's third state when step 4 touches this
  surface.

## What shipped 07-24: step 2 — Custom profile + guard model + the G4 anchor caller

Built from a frozen contract that was **adversarially reviewed before any code**
(verdict: amend-then-build; six MUST-FIX findings integrated — the review caught
two real safety holes the draft missed: a targeted restore silently re-weakening
guards, and a session destructive grant surviving a switch back to Simple).
Coordinator personally reproduced four mutation kills (CUSTOM→SAFE derivation,
session-grant leak into `_grants`, guards.set ignoring a mint failure, dedupe
removal); every regression test in the wave is mutation-proven.

- **`ProfileId.CUSTOM`** (profiles.py) — Developer's surface, `advanced: true` on
  the wire; the frontend hides it behind an "Advanced…" disclosure + two-step
  confirm. `mode_for_profile`: DEVELOPER **or CUSTOM** → OPEN (policy.py; a
  SAFE-derived Custom would have nothing to tune). `profile.get`'s `mode` stays
  `'safe'|'open'` — never `'custom'`; the guard panel keys off the profile.
- **`GuardConfig`** (policy.py) — two closed vocabularies with total strictness
  orders: `guard_destructive_card` `per_invocation` > `session`;
  `guard_auto_grant_scope` `none` > `non_destructive` > `everything`. Defaults ≡
  today's OPEN, and `authorize(guards=None)` ≡ defaults — that equivalence is the
  Simple/Developer freeze, proven by the whole pre-existing suite passing
  untouched. Guards are EFFECTIVE only under Custom (`_effective_guards`, the ONE
  resolution function all three authorize call sites read — orchestrator, routine
  engine, widget Run pill).
- **"Ask once" lives in a dedicated set.** A `session` destructive approval is
  remembered in `_destructive_session_grants`, NEVER `_grants` — the SAFE
  `check()` path reads only `_grants`, so the grant is structurally invisible to
  Simple. Belt: `profile.set` now calls `revoke_all()` + `clear_denials()` on
  every switch (the revoke_all docstring's own posture principle).
- **`guards.set` is the G4 anchor caller** (rpc/guards.py): validate → compute
  weakenings → **mint the anchor FIRST** (refuse the whole set, nothing persists,
  if the anchor cannot mint) → persist. `mint_anchor` gained fingerprint
  **dedupe**: one anchor per distinct weakening save; weaken→tighten→weaken
  churn cannot grow an unbounded permanent list, and a crash between mint and
  persist re-mints nothing on retry.
- **Restore re-weaken disclosure** (rpc/snapshots.py): when a restore lands on a
  weaker guard posture under Custom, the result's `detail` says so in plain words.
  No new anchor — the original weakening's anchor is undeletable and still there.
- **`created_in_mode`:** artifacts stamp `'open'` under Custom (Custom IS
  OPEN-derived; the three hard-coded `== 'open'` hiding/refusal filters keep
  working, so Custom-built widgets/routines hide in Simple). ONLY
  `config_snapshots` records `'custom'` (main.py `mode_ref`) — display-only, C6
  never filters.
- **Frontend:** Advanced disclosure + two-step confirm on the profile card; the
  Custom guard panel (two guards only — the floors are structurally absent from
  the panel), frozen plain-language copy including the honest cost of "Never ask";
  weakening saves get the permanent-anchor confirm, tightening saves go straight
  through; `ipc.restoreSnapshot` finally has its caller — per-row "Restore this
  one" on PERMANENT rows only (owner decision 2026-07-24).
- Also: dropped the never-written `RestoreResult.providers_needing_a_key`
  (loose end resolved: the keychain probe computes names itself); amendment §13
  **Q3 closed** as the lean (reachable from any profile, deep + questioned).

**The post-build rigor pass (same day) — read this before trusting the wave
above.** A second adversarial reviewer attacked the finished code with
reproduce-don't-read rules and found **one real bug the first review, both build
agents, and 25 green tests all missed**: `auto_grant_scope='none'` — the
STRICTEST-labelled option — routed destructive calls into the coarse SAFE flow,
so one approved `ls` silently covered every later `rm -rf` with no card and no
command text, under copy promising "asks before every kind of action", and
counted as a *tightening* so no anchor was minted. Fixed: destructive never
enters the coarse flow under any scope; the scope knob governs everyday actions,
the card knob alone governs destructive ones ('everything' stays the one explicit
override). Two regression tests pin it, both proven red against the reverted fix.
The lesson is the standing one: the test gap was structural — the only
scope-'none' test used a non-destructive tool, and its one-arg stub would have
TypeError'd on the destructive path, so the wrong assumption protected itself.
Also from the pass: `guards.set` now persists its two keys in ONE commit
(`Store.set_settings`; half-a-pair after "nothing was changed" was a lie
waiting); anchor dedupe refuses to confirm an anchor whose payload no longer
loads (row rotted + sidecar gone → fresh mint); the D7 docstring now names the
one path that legitimately skips the notice (sidecar cold-start — the
pre-restore posture is unknowable there); the "Ask once" copy now states its
real breadth ("anything else it does goes ahead without asking"); TESTING-
CHECKLIST **§13b** is the manual QA script for all of this; and a **live
end-to-end driver run** (the HANDOFF pattern, 17 checks, including one real
haiku turn) verified the whole Custom flow over real JSON-RPC — dispatch,
anchor, dedupe, D7 notice, C6 under SAFE, and `snapshot_now` writing through
`main()`'s late-bound holder.

## What shipped 07-24: steps 4 + 5 — free-model endpoints & the coding harness

Built by four agents in isolated worktrees from two frozen contracts, each
adversarially reviewed twice before a line was written, then merged four ways by
hand. **Read the post-build pass below before trusting any of it** — it is where
the real defects were.

**Step 4 — free-model endpoints, add-by-prompt, "make it cheaper".**
- **`agent_core/net_vetting.py` is new and is the load-bearing piece.** The WHOLE
  pinned-request execution moved out of `read_web_page` — not just the URL
  rewrite. Reusing only `pinned_url` would make httpx verify the certificate
  against the IP literal and refuse every legitimate HTTPS server, or tempt
  someone to weaken verification, which is a worse hole. The vetting DECISION is a
  parameter (`allow_private` / `require_default_port`), so the public-web policy
  and the user's-own-LAN-host policy share one mechanism; the plain sentences are
  a parameter too, because the two callers speak to different audiences.
- Both flows are **propose/confirm RPCs whose fields are core-derived or canned** —
  the turn reply never carries a model-authored actionable payload. `endpoint.propose`
  reads the CURRENT turn's `role=="user"` messages only (a model that echoes
  `https://evil` into its answer must not become the extraction source), and only
  a short add-endpoint-shaped utterance arms a card. `costPlan.propose` is entirely
  constants.
- `costPlan.apply`: validate → skip if already in effect → **snapshot, REFUSING
  the whole apply if it cannot mint** (a deliberate new hook class — a compound,
  conversationally-initiated degradation for the at-risk persona whose only
  recovery is the restore point; `routing.set` still proceeds-with-warning, and
  the asymmetry is noted in both places) → **one atomic `Store` commit** so a
  half-applied plan is impossible.
- The free chip stays **Ollama-only**: no cloud `CloudModel.free` is True in v1, so
  the chip asserts a cost fact Addison can actually establish. Google's free tier
  is *information* under the provider row, not a routing flag.

**Step 5 — the coding harness + workspace-trust.** Two typed, OPEN-only,
path-bounded file tools, a `workspace_trust` table, a `workspace.*` RPC, and three
new Rust bridge methods. Four things are worth internalising before extending it:

- **Confinement is a DIFFERENT PREDICATE from prompting, and that was the central
  gap in the first draft.** "Is this path inside a trusted root" (permission to
  TOUCH) is not "may the card be skipped" — and a LOW read never cards in OPEN
  anyway, so the gate's `trusted` bool alone confines nothing. The CALLER
  (orchestrator / routine engine) resolves `affected_path`, checks it, and
  **hard-refuses before `execute`** for LOW and MEDIUM alike.
- **Resolve ONCE.** `affected_path` realpaths exactly once; the resolved value is
  what the caller checks AND what `execute` acts on, handed over via
  `ExecutionContext.resolved_path`. Re-reading `args["path"]` inside `execute`
  reopens a TOCTOU gap: confinement approves one path, the write lands on another.
- **`dev_only` split into two dimensions** (`open_only` = visibility,
  `allow_missing_undo` = the exemption from the undo-at-registration check),
  because `write_project_file` must be BOTH hidden from SAFE AND undo-enforced,
  and the old single flag could not say that.
- **Owner decision 2026-07-24: trust suppresses cards ONLY for the typed,
  path-bounded, undoable file tools. `run_command` ALWAYS cards.** Its
  `affected_path` is None, so confinement never governs it and it can never be
  trust-suppressed. That is what makes amendment §8.2's two bullets simultaneously
  deliverable. **§8.2 and design-doc §9 are both annotated as superseded** — trust
  is NOT snapshotted (see below), and the OPEN tools scope by trusted root rather
  than by file picker.
- **Trust is EXCLUDED from snapshots**, on the `tool_grants` precedent: standing
  consent that suppresses cards is a grant in all but name, and restoring one the
  user had revoked would be privilege escalation delivered by the deliberately
  ungated one-action restore button.

### The step-4/5 post-build rigor pass — read this before trusting the above

Three adversarial reviewers attacked the finished tree with reproduce-don't-read
rules. They found **seven real bugs that two rounds of contract review, four build
agents and 847 green tests all missed**, plus a cluster of tests that proved
nothing. Every fix below is mutation-proven: **27 Python mutations applied, 27
killed**, plus 2 Rust mutations killed, each reverting one fixed line in a scratch
copy outside the repo. The coordinator reproduced the two worst personally, from
scratch, before touching anything.

**The two that would have shipped a broken feature and a leaked key:**

1. **The API key was forwarded to whatever host a redirect named.** `open_vetted`
   built the header dict once and threaded it through every hand-followed hop, so
   a custom server — or anything able to answer 302 for it — harvested the user's
   key verbatim. The aggravating detail: **httpx's own follower strips
   `Authorization` cross-origin** (`Client._redirect_headers`), so the hand-rolled
   loop that replaced it was strictly weaker than the library it displaced, on the
   one axis that carries a secret. Fixed with a SEPARATE `credential_headers`
   parameter rather than a "strip anything called authorization" rule, so the next
   caller to put a secret in a header inherits the protection by construction
   instead of by naming their header correctly.
2. **`pinned_url` dropped the PORT**, so `http://localhost:11434/v1` connected to
   `127.0.0.1:80` — a different service on the same machine, carrying the Bearer
   key to it. Harmless while the only caller required the default port; live the
   moment step 4 allowed any port, which means **the entire feature — Ollama
   :11434, LM Studio :1234, llama.cpp :8080 — could not work at all.** The default
   port is still omitted, exactly as a browser omits it, so `read_web_page`'s
   requests are byte-identical to before.

**The rest, each reproduced before it was believed:**

3. **A NUL byte in a `path` argument crashed the whole turn** — `Path(raw).resolve()`
   raises, and both confinement call sites sit OUTSIDE the handling that exists so
   "a tool failure is a failed STEP, never a crashed turn". On the routine path it
   left the run recorded as `running` forever. Now an unresolvable path returns a
   **sentinel, never `None`** — because `None` means "not a path tool" and skips
   confinement entirely, which would have let a malformed argument walk past the
   boundary into the gate.
4. **`workspace.list` was read as `{roots}` while the core sends `{folders}`**, so
   the trusted-folder list rendered permanently empty in the shipped app: no "Stop
   trusting" button, standing consent unrevocable from the UI. Both suites were
   green — Python asserted `folders`, vitest parsed a hand-built `{roots: […]}`
   literal, and **neither could see the other**. Fixed, and closed structurally: the
   generated payload fixtures now cover `workspace.list`, `costPlan.propose` and
   `endpoint.proposeFromConversation`, so a new payload a parser consumes gets an
   artifact both sides share. *Add a fixture for every new payload.*
5. **A turn-scoped "Not now" was ignored inside a trusted folder** (`_auto_grant`
   never consulted `_denied`). Nothing escalated — the call was card-free anyway —
   but a person was shown a card, said no, and watched Addison edit a file in the
   same turn. Consent honesty, not privilege.
6. **Workspace trust silently overrode Custom's strictest guard.**
   `auto_grant_scope='none'` is the maximum-asking option and its copy says Addison
   asks about everything; trust made destructive writes card-free under it, and a
   tightening mints no anchor, so nothing marked the moment. **Exactly the defect
   shape the step-2 rigor pass found** — the strictest-LABELLED option carrying the
   quiet hole. Simple/Developer are byte-for-byte unchanged (their guards are the
   defaults).
7. **`trust_env=False` was applied to the stock OpenAI connect**, which is a module
   constant, not user input — so connecting an OpenAI key behind a corporate proxy
   would fail while chat kept working. A freeze break dressed as hardening.
8. **The connect card's worst case went from ~20s to ~120–240s**: the walk tries up
   to `MAX_ADDRESS_ATTEMPTS` addresses per hop across every redirect, and the
   idempotent retry then re-ran the whole thing. A per-socket timeout is not a
   budget — same lesson as the step-3 fallback budget — so `total_timeout` now
   bounds the whole walk, **including the address loop inside each hop** (bounding
   hops alone left most of the wait unbounded; the first version of this fix did
   exactly that and its own mutation test caught it).
9. **The Rust shell's data-dir floor was defeatable by a DANGLING symlink** — the
   target does not exist, so canonicalization stopped at the link's own harmless
   location while `fs::write` followed it and planted a file in the G3 sidecar
   directory. The Python floor caught it, so this was never a live breach, but the
   comment claiming defence-in-depth was false. `canonical_lossy` also only checked
   the IMMEDIATE parent, so any missing intermediate component left the candidate
   un-canonicalized while the protected dir was canonicalized — on macOS, where
   `/tmp` and `/var` are themselves symlinks, that is the ordinary case.
10. **Smaller, all real:** the `_ADD_ENDPOINT_HINTS` gate matched **substrings**, so
    "add" matched **Addison** — the app's own name — and "api" matched "therapist";
    `"Addison, what is <url>?"` armed a connect card, and deleting the entire gate
    left the suite green. Now word boundaries, with two hints dropped for carrying
    no signal. The case-insensitive URL regex fed a case-SENSITIVE scheme check, so
    a phone's `Http://…` was refused with "Enter a web address that starts with
    http://" — false about the address just typed. The protocol drift test's
    `[a-z]+\.` namespace pattern matched **neither** `costPlan.*` constant, so the
    one guard standing in for codegen ignored the two newest methods.
11. **Tests that proved nothing.** Five mutations survived the *entire* 847-test
    suite, and the reason was structural: **pytest's `tmp_path` is already fully
    realpath'd**, so in every step-5 test the raw argument and the resolved path
    were byte-identical and the whole resolve-once mechanism could be deleted
    unnoticed. The fix is a symlinked alias in the fixture, so the tool is handed a
    path it must normalise. Also unwatched: `apply_cost_plan`'s atomicity (the one
    property its dedicated `Store` method exists for), R7's "both halves must
    hold", and the routine engine's confinement, which had only a positive test.

**Frontend integration completed in the same pass:** the step-4 cards were built
and tested but rendered nowhere — now wired through a `useOffers` hook mirroring
the widget propose→card→confirm flow, triggered off the USER's text only (the
model's reply must never arm a card; the core enforces the same rule). The Google
free-tier line was an `<a href target="_blank">` **that could not open anything** —
the Rust shell registers exactly three commands for the webview and none is
`openExternal`, and `Markdown.tsx` states the standing rule that the webview never
opens URLs itself. Its test asserted the `href` and passed while the control was
dead. Now selectable mono text the person can copy, with a test pinning the absence
of an anchor. A throwing post-turn drafter also used to stamp a **successful** turn
`failed: true`; isolated now.

## What shipped 07-24 — the security + test-hardening wave (#48, #49)

After step 1 merged (#47), a test-quality measurement turned up a **live security
bug**, which is the reason this wave exists.

**#48 — `run_command` auto-granted destructive commands (LIVE BUG, fixed).** In
OPEN mode the gate auto-granted anything the tool's classifier called read-only,
and that classifier was defeatable three ways, each a character or flag its
blocklist did not anticipate:

```
ls\nrm -rf ~/x      shlex treats \n as whitespace, so it read as a lone `ls`
ls & rm -rf ~/x     the metachar list had && but not bare &
find . -delete      an allowlisted reader with a destructive primary
grep -rf /etc/x .   a short flag defeated by bundling
file -Cm /tmp/x     an allowlisted reader that WRITES a compiled magic file
```

The blast radius is the filesystem, which is **outside G3** — an `rm -rf` is not
undoable. **Owner decision: statically deciding whether an arbitrary shell command
is read-only is a losing game, so the auto-allow was removed rather than patched.**
`is_destructive` now returns `True` unconditionally; every command raises the
per-invocation card showing its exact text. The classifier, the read-only
allowlist and the metacharacter list are **deleted** — dead once nothing
auto-grants, and their absence removes the false confidence that any of it was
trusted. Cost is a card on every command including `ls`; that is the intended
trade. An argument-allowlist was drafted and rejected — a hardening round showed
even that was defeatable, which is what drove the decision.

**#49 — the test gaps that would let a floor breach ship green.** A triage pass
reproduced every candidate against the real code first and found **no further live
bugs**; everything below was correct code with nothing watching it.

- **`keychain.rs` / G1 — the headline.** Two tests built a `json!` literal in the
  test body and asserted on it, never calling the real `handle()`. So adding the
  ed25519 **private seed** to the real `getDeviceKey` response **passed all 31
  Rust tests** — on the most sensitive value in the system, in the highest-trust
  process. Response builders are now extracted (behaviour identical) and the tests
  assert over them, plus a sweep serialising every keychain response and asserting
  the seed appears in none. Verified failing with the seed added.
- **dev-only ⟹ OPEN-only, enforced at DISPATCH.** `visible_tools(SAFE)` hides
  dev-only tools from the *model*, but hiding is not enforcing: a `tool_use`
  naming a hidden id still reached `registry.get()`, and the gate does not check
  dev-ness. A dev-only tool **with no self-check executed under SAFE** through both
  dispatch paths. The boundary held only because `run_command` refuses inside its
  own `execute` — a convention tool #2 would not inherit, with steps 5, 7 and 8 all
  adding dev-only surface. Both dispatch sites now consult
  `registry.refuse_if_dev_only_outside_open()` **before the gate**, so nobody is
  asked to approve something that was never going to run.
  `tests/test_dev_only_boundary.py` drives a rogue HIGH dev-only tool through both
  paths in both modes — and asserts it **still runs in OPEN**, because breaking the
  harness would be worse than the hole.
- **undo substance.** `undo = "a string"` registered at HIGH straight into the SAFE
  view, where it would fail at the moment somebody needed to reverse something. Now
  refused. A *callable* no-op cannot be caught statically — the comment says so
  rather than implying otherwise, and a round-trip test is the honest answer.
- **repo-wide G2** (`tests/test_g2_no_self_trigger.py`): the only test pinning
  "Addison never triggers itself" AST-scoped `snapshot_manager.py` alone. Now every
  core module, with the rule stated as *"nothing that fires work on a SCHEDULE or
  after a DELAY"* rather than a ban on concurrency — so the legitimate worker
  thread, `Event` waits and blocking `queue.get()` stay green and the test does not
  get deleted by the next person it annoys. Its anti-vacuity check pins the
  **subpackages** covered, not a module count: a count lets you drop `providers/`
  entirely and stay above the floor.
- **`shell_bridge`** (killed 0 of 3): error frames, timeouts, and
  `get_provider_key` now covered. Its G1 retention test checks the instance, the
  **class**, and the **module** namespace — the plausible mistake being to port the
  Rust shell's sanctioned session cache into the core as `type(self)._cache`.
- **`rpc/widgets`** (killed 0 of 2): both SAFE-enforcement call sites, asserted
  against the widgets **table** rather than `widget.list` — the render filter hides
  the mutation's row, so the list looks identical either way.

**Also #49: `ruff` is pinned to `>=0.15,<0.16`.** CI failed with 183 lint errors
while the same tree was clean locally. Not the code: `pyproject.toml` asked for
`ruff>=0.6`, CI installs from it, and **ruff 0.16.0 shipped with more rules on by
default** — verified at **182 errors against an unmodified `master`**. The first PR
opened after the release inherited a failure it did not cause. Raising the bound is
now deliberate: bump it, run ruff, and adopt or configure the new rules in the same
change. The 182 are a separate decision (see Known gaps) and **must not be
bulk-fixed** — many `BLE001` hits are the deliberate broad `except` in the recovery
paths, where swallowing is the point.

## What shipped 07-25 → 07-26 — the dark v4 redesign wave (PR #58)

**Merged to `master`** as `a22badd` on 2026-07-26. The commit-by-commit account
that used to live here is in git; what survives below is only what a later
session still has to honour.

**Design authority: `docs/design-brief-dark/`.** `README.md` + `prototype.html`
are the designer's reference and **`IMPLEMENTATION.md` records the binding
prototype→app mapping**. Read that file before restyling anything — it is where
"demo content is never shipped, real features are restyled and never de-wired" is
written down.

**Rules the rigor pass (`1241026`) established — these are safety, not polish:**

- **A pending consent card is hoisted above any modal/drawer scrim, and the
  modal's focus trap deliberately includes it.** A consent surface stranded
  behind a scrim is unanswerable, which makes it a safety failure, not a cosmetic
  one.
- **The restore footer's undo promise is mode-scoped** (`footerNote` in
  `RestorePointsModal.tsx`). `run_command` is SAFE-2's one explicit exemption
  under OPEN, so a blanket "everything can be undone" contradicted the profile
  card two sections away.
- **The one-action restore names its target and timestamp through the confirm.**
  Losing that was a *regression against `master`*, on the G3 surface itself.
- Light-mode text meets AA; a reveal never scroll-jails a reader who has scrolled
  up; the favicon is a raster rendered from a checked-in master so the mark does
  not depend on the rasterising platform's fonts.

**What the tests were doing — the standing failure mode, found again.** The
frontend suite went 238 → 302 because `Composer` **had no test file at all and
all 11 mutations against it survived**, the reduced-motion guard could be deleted
with the suite still green, and **two G3 tests passed while restoring the wrong
snapshot.** Same shape as the step-1 finding. Assume it is still true somewhere.

**`ee38dbe` also redefined Phase 3.** `docs/phase-3-review-surface-plan.md` (a
Developer/OPEN review surface, approved 2026-07-25, blocked on steps 6–8) is now
part of that phase alongside packaging, signing, notarisation, the updater,
binary restore and Secure-Enclave identity. The redefinition was written back
into the four documents that had scoped Phase 3 the old way.

### Shipped: thread windowing (`839bcff`)

**This section previously said the opposite.** Thread windowing came in with #58
and is on `master`; the `windowed-thread` branch and its `8503b18` no longer
exist. If a doc or comment implies the thread renders the whole conversation, it
is wrong.

Opening a conversation used to render react-markdown (and mermaid) for every
settled message in one commit. The thread now renders a trailing window of 30
rows and extends upward by 30 when the reader reaches the top, holding scroll
position across the prepend — 272 DOM nodes instead of 3602 on a 400-message
conversation, and 30 markdown parses instead of 400. Nothing below 30 messages
changes. The window is a count of rows hidden at the **top**, never a trailing
slice: a message arriving mid-stream must lengthen the window at the bottom
rather than drop the topmost rendered row from under someone who has scrolled up.
Pinned by `shell/src/__tests__/threadWindow.test.tsx`.

## What shipped 07-26 — after PR #58, direct on `master`

Four commits, all owner-reported from a running app rather than found by a gate.

- **`cc70ea8` — the icon pipeline was building onto a white plate.** QuickLook,
  the rasteriser both build scripts use, fits art top-left and pads the rest of
  the canvas with **white** (measured: 37px of 256), and flattens onto white — so
  every icon carried a white band down its right edge and along its bottom, and
  the app icon's rounded tile came back sitting in an opaque white square. Giving
  both masters a percentage size with an explicit `preserveAspectRatio` fixes it.
  The cause was under the whole pipeline, not in the artwork.
- **`e98828c` — the pointer glow is reverted.** Owner decision: it was
  decoration, and the accent is reserved for actions, selection and live state.
  Removed whole — component, stylesheet block, the mount in `App`, its five
  tests — so `styles.css` is byte-identical to before it landed and CLAUDE.md
  needs no exception written for it. The icon fixes from the same commit stay.
- **`3ab1159` — the widget rail said everything twice.** It draws pinned widgets
  *and* adds core-computed rows (the token meter, the connection list), with
  nothing stopping both from showing the same source: with the seeded
  "Connections" widget pinned the rail read Ollama, Anthropic, the meter, then
  Ollama and Anthropic again. Ambient rows now stand down for any source a
  **pinned** widget already shows — pinned only, because a widget in the
  collapsed tray is not on screen and the ambient row is then the only place
  those facts appear. Same commit fixed both model menus vanishing for a frame.
- **`07cc9ee` — the empty-state starfield is gone.** Reported as "a pixel or
  something akin to it" beside the greeting. It was not an artifact: it was the
  prototype's starfield, implemented as the exact radial-gradient stack from
  `prototype.html:72`, and the brief does ask for it. At 1280x800 it is five 1px
  dots over a 464x276 box, two of them landing on the type. At that density it
  never reads as a field — it reads as dust, or as a dead pixel next to a word.

## What shipped 07-26 — per-token streaming (`streaming-replies`)

Owner report: replies did not appear as they were written, they just appeared.
Two commits, and the first is a bug fix, not a prerequisite chore.

### `d2174c1` — the reveal never ran in production

`d8493e6` (dark v4 wave, above) added a whole-answer scramble reveal for the
stated reason that "the core does not stream". **It never executed on a real
turn**, and the reason is worth keeping written down because three comments and a
test all asserted the opposite premise:

- The core DID emit `conversation.streamChunk` — once per turn, with the entire
  finished answer, from `orchestrator.stream_to_frontend(response.text)`.
- `conversation.sendMessage`'s **result carries no answer text** (only
  `userMessageId` / `assistantMessageId` / `answeredWith`), so that one chunk was
  the sole delivery path for the reply. `extractFinalText` returned null.
- So `revealFinalText`'s guard (`!streamTextRef.current && finalText`) was false
  on both halves, and the engine `appendStreamedText` had just started was torn
  down by `runTurn`'s `finally` a frame later. One 15-character frame of noise,
  then the whole answer.

Fixed in `useTurn`: an engine still mid-resolve when the turn settles is animating
text that has fully arrived, so it is promoted to a reveal and its `onDone`
releases the overlay. Catching up **before** the turn settles stays a pause
between deltas and keeps the engine — dropping it would hand the next chunk a
fresh engine that re-animates the whole accumulated prefix from character zero.

### The second commit on the branch — real streaming, all four providers

`ModelProvider.send` gains `on_delta: DeltaSink | None`. A provider that streams
calls it per piece of prose and **still returns the same complete
`ModelResponse`**, so the tool loop, `usage_log` recording and the fallback chain
cannot tell the two paths apart — streaming stays a transport detail, and the
`effort` precedent (accept-and-ignore) covers every provider that can't do it.

Two rules bind every implementation, and the tests are built around them rather
than around "some deltas arrived":

1. **What is streamed equals what is returned.** Every provider test asserts the
   joined deltas against `response.text`.
2. **Only prose reaches the sink.** Anthropic `input_json_delta` and
   `thinking_delta`, OpenAI fragmented `function.arguments`, Google
   `functionCall` parts — all accumulated silently, never shown.

Shared primitives in `providers/base.py`: `open_stream` (uses
`client.send(request, stream=True)`, which composes with `request_with_retry`
unchanged — `idempotent=False` still retries only the connect-level failures that
prove nothing was generated or billed) and `iter_sse_json`.

Per-provider notes worth not rediscovering:

- **Ollama streams NDJSON, not SSE**, and its streaming is **conditional**: a
  model on the non-native **fallback** path returns tool calls as a fenced JSON
  block inside ordinary reply text, so streaming it would put
  `{"tool": "delete_file"}` on screen as though Addison had said it. Whether a
  piece of that text is prose or machinery is only knowable once the block closes,
  so `native or not tools` gates it. Not a limitation to remove later.
- **OpenAI needs `stream_options.include_usage`** or a streamed turn reports no
  tokens at all and §4.8's substrate silently loses it.
- **Google's `usageMetadata` is cumulative, not additive** — last frame wins.
  Summing frames would over-report every streamed turn into the token meter.
- **Setup Assistant relay accepts and ignores the sink**: its requests are SIGNED
  over the assembled body (§5), so a streaming variant is a change to the relay's
  contract, which lives outside this repo. Its answers arrive whole and get the
  frontend reveal.

**New honesty rule on the routed path.** Once a delta has been shown, a
`ProviderUnavailable` may **not** fall forward: the next candidate returns a
COMPLETE answer, which appends to the partial one and yields a single message that
reads as one answer and is two. Nothing can un-say what was shown, so the turn
fails with that provider's own sentence. Same reasoning as the existing
`committed` cross-provider forbid. A stream that dies **before** emitting anything
falls forward exactly as before — that case is tested too, so the rule cannot
quietly become "streaming disabled fallback".

**Visible side effect, deliberate:** a tool round's preamble text ("Let me look
at that…") now reaches the reader as it arrives, where previously only the final
answer was pushed. It appends to the same message.

`supports_streaming` on `ProviderCapabilities` is **declared and read by
nothing** — verified by grep. It was not repurposed as "honours `on_delta`",
because Ollama's answer to that is conditional; the reasoning lives at each
`send()` instead.

## Next up — Phase 2 (code), in dependency order

**The step-1 ledger is RETIRED (2026-07-24).** The two pre-step-2 items shipped
on the `retire-step1-ledger` branch, each with mutation-proven tests:

- **`snapshot_now`** — shipped as a **LOW, capture-only** registry tool
  (`agent_core/tools/snapshot_now.py`), in `_V1_TOOL_IDS` so the companion gets
  it. Late-bound `Callable[[], SnapshotManager | None]` wired through
  `build_registry()` + a holder filled after server construction in `main()`;
  answers *"I can't save a restore point just yet"* before the store is up; a
  successful save clears the sticky capture-failure warning, matching the
  Settings button. An AST source test forbids every manager verb except
  `capture` in the module. `docs/architecture.md` / `docs/classes.md` now
  describe the shipped tool.
- **The Restore card says so when there is NO verified restore point**
  (`SnapshotsCard.tsx`): two client-derived sentences — no row verified (G3
  silently off until the first completed turn) vs. some row verified but no
  target (everything saved matches the running config). The core's
  'unreadable' walk outcome is indistinguishable from 'identical' on this wire
  — accepted, commented in the component; the wire's `why` field is the future
  fix if that distinction ever has to be drawn. (Salvaged from the scrapped
  "doctor command" — see git history for why doctor contradicted G3.)

Then:

1. ~~**Snapshot/restore subsystem (G3)**~~ — **DONE** (see the section above).
2. ~~**Custom profile + guard model + undeletable anchor**~~ — **DONE
   (2026-07-24; see "What shipped 07-24: step 2" above).** Shipped with the TWO
   real guards only, per the scoping finding — the panel grows a
   workspace-trust guard at step 5 and a keyword-gate guard at step 8 **as those
   capabilities land, never before** (a toggle that controls nothing, in a
   safety panel, is a lie in the worst possible place). All four step-1
   leave-behinds found their consumer: `mint_anchor()` has its caller
   (`guards.set`, now with fingerprint dedupe), `created_in_mode='custom'` is
   written (snapshots only), `guard_weakened` rows are minted, and
   `ipc.restoreSnapshot` drives the per-row restore on permanent rows.
3. ~~**Routing strategies**~~ — **DONE (2026-07-24; see "What shipped 07-24:
   step 3" above).** Balanced cut from v1 by owner decision (amendment §10.1);
   the confidence half of §13 Q5 stays v2 substrate.
4. ~~**Free-model endpoints**~~ — **DONE (2026-07-24; see "What shipped 07-24:
   steps 4 + 5" above).**
5. ~~**Harness + workspace-trust**~~ — **DONE (2026-07-24; same section).** Note
   the shipped shape is narrower than this line described: inside a trusted dir
   the gate stops prompting for the **typed path-bounded file tools only** —
   `run_command` always cards (owner decision).
5.5. **Containment for the OPEN harness** — **PROPOSED 2026-07-26, not started.**
   [`docs/step-5.5-containment-plan.md`](step-5.5-containment-plan.md). Step 5's
   unfinished half, not new scope: it shipped a shell without re-establishing the
   property design-doc §9's first mitigation protected, and that bullet — unlike
   the picker-scoping bullet beside it — was never amended. **The concrete
   consequence: `run_command` has `affected_path = None`, so confinement never
   governs it, and one approved command deletes the database, the sidecars and
   every `undeletable` anchor. G3's "the restore path is itself unbreakable" is
   true in SAFE and overclaimed in OPEN**; CLAUDE.md now carries that qualifier.
   Ship the pre-gate denylist first (hours, and it makes G3 true against the
   obvious case) and the Seatbelt work after. **Step 7 is downstream of its audit
   log**, because amendment §8.5 promises MCP tools are "gated, logged,
   undo-aware" and no log exists.
6. **Widget capability tiers + expanded safe vocabulary**; make `primary.txt`
   capability-aware.
7. **MCP client** tools through the registry + gate (SAFE: read-only/undo-able
   only — invariant 2 enforces it).
8. **Automation keyword gate** + author-OS-run automation.

**5.5, 6, 7 and 8 are what is left.** 5.5 finishes work that is already merged;
6, 7 and 8 are the new capability.
Downstream of all three sits **`docs/phase-3-review-surface-plan.md`** (approved
2026-07-25) — a bespoke review surface for the Developer/OPEN profile, explicitly
blocked on 6, 7 and 8. It also redefines Phase 3: that phase is no longer only
packaging / signing / notarisation / the updater / binary restore /
Secure-Enclave identity, and the four documents that scoped it that way were
amended to say so.

Steps 3–4 (companion-facing) can run in parallel with 5–8 once 1–2 land.
Close the amendment's **§13 open questions** as you go. **Three are now closed —
do not relitigate them**, the reasoning is recorded inline in §13 and summarised
below. **Q3 (Custom reachability) closed 2026-07-24 with step 2** — as the lean:
reachable from any profile, behind an Advanced disclosure + two-step confirm
(recorded inline in §13). Still open: keyword syntax (Q1), auto-routing
depth (Q5), MCP-in-SAFE constraint (Q6), widget capability declaration (Q7).

### How step 1 was verified — the standard to hold for steps 2–8

**The first round of step 1 shipped with all eight CI gates green while the
headline requirement was broken.** That is the fact to carry forward. The tests
had been written by the same agents, against the same assumptions, as the code
they were meant to catch — so they asserted the wrong behaviour confidently and
passed. Green gates proved the tree was self-consistent. They proved nothing
about whether it was right.

What closed it, and what steps 2–8 should repeat:

- **An adversarial review pass that reproduces, not reads.** The subsystem was
  reviewed by an agent whose brief was to break it, then independently
  re-verified by a second one that re-ran every original reproduction plus fresh
  attacks of its own.
- **Mutation testing on the regression tests.** **11 source mutations** were
  applied — each one reverting a single fixed line — and **all 11 were killed**:
  every regression test failed with its fix reverted and passed with it in
  place. That is the property that matters, and it is the one a normal green run
  cannot tell you. *A test that passes both before and after its own fix is
  worthless, and it is exactly what let round one ship.* Revert the fixed line in
  a scratch copy **outside** the repo, watch the test go red, put it back.
- **The coordinator reproducing the headline defects personally** rather than
  taking an agent's report on trust.

For a floor, budget for this explicitly. It cost roughly as much as the build.

### Residual walk-position defects (N-1 / N-3 / N-4)

The rollback walk's *position* — how far back repeated clicks have got — was the
last cluster of defects. As of this handoff the fixes are **in the tree** and the
suite is green; verify against the code, not this paragraph:

- **Position is held by row identity, never by fingerprint** (`_walk_start`). A
  user toggling a setting and toggling it back puts the same fingerprint in the
  list twice; anything locating the position by fingerprint locks onto the newer
  occurrence and oscillates between two configurations forever, with everything
  older unreachable. `refs` only ever grows at the top, so a remembered row's
  index can move down but never up.
- **The position survives a relaunch**, via `meta.restored_to` on the
  `pre_restore` row (`_recorded_restore_target`, documented in `data-model.md`).
  In memory alone it would be lost, and a user coming back an hour later would be
  handed the config they had escaped. Memory is the fast path; disk is the truth.
  Only the **newest** `pre_restore` row counts — an older one describes an older
  restore, and honouring it would rewind the walk.
- **The position expires on its own.** It holds only while the current config
  still fingerprint-matches the row that was restored, so any user change ends
  the walk and the next click starts from the top. That also cleans up after a
  restore whose apply failed: the marker was written before the apply, the config
  never moved, the fingerprints disagree, the marker is inert.
- **The sidecar arm runs mid-walk, and is given the walk position rather than
  being switched off.** An earlier design gated the arm on the walk having
  started at the top; that was rejected, because the one situation the arm exists
  for is the database being the damaged part, and a user two clicks into a
  rollback is exactly who needs it. So `restore_last_working()` skips the arm on
  only one outcome — `'bottom'`, where verified rows exist and the user has
  already walked through them, so stepping past their own proven configs into an
  unproven one is a choice that belongs to them. On every other outcome the arm
  runs, and the position is passed down into `_restore_from_sidecars`, which
  filters the directory through `_payloads_below`: only payloads strictly OLDER
  than the row the last restore landed on are candidates. Without that filter the
  arm reads the whole directory and happily applies one of the newer payloads the
  walk deliberately stepped past — the user presses "go back", gets sent forward
  into the setup they were escaping, and is told the ordinary success sentence
  while it happens. The position expires the same way the database walk's does:
  it holds only while the user is still sitting on the config that row restored,
  so any change ends the walk and the whole list is fair game again. Read the
  docstring on `_payloads_below` before touching this.

Tests to look at first: `test_repeated_restores_walk_further_back`,
`test_a_configuration_the_user_returned_to_does_not_trap_the_walk`,
`test_the_walk_remembers_where_it_got_to_across_a_restart`, and
`test_a_turn_after_a_restore_does_not_walk_the_user_forward_again`.

### G1 hardening, pulled forward from step 4

A `base_url` is captured by every snapshot, so it lands in `state_blob`, in the
plaintext sidecar, and — through any permanent row — **forever**. Waiting until
step 4 would have meant shipping a floor that archives secrets, so the fix moved
into step 1. `_base_url_problem` in `rpc/providers.py` refuses, at the door:

- **userinfo** — `https://user:sk-…@host`;
- **any query string or fragment at all.** A base URL needs only
  `scheme://host[:port][/path]`, so this is a bounded rule rather than a list of
  suspicious parameter names. An earlier draft *did* blocklist names
  (`key`/`token`/`secret`/…) and was beaten in review by `?sk=` and `?t=` — the
  attacker picks the name, so a name list loses by construction. Recorded because
  the same reasoning applies to anything similar later;
- **key-shaped path segments** — a known opening (`sk-`, `pk-`, `ghp_`, `gsk_`,
  `xai-`, `bearer`) or a long, mixed, high-entropy segment.

Refusal is at the door, deliberately: sanitising on capture would make a restore
write back a different address than the one configured and silently break the
user's server.

**Residual, and worth closing in step 4 — note this is NOT "the path is
unchecked".** The path *is* checked; what leaks is the shape of the check. A
segment escapes when it fails the composition gate or sits under the entropy bar:

| Accepted today | Why it slips |
|---|---|
| `…/v1/qwrtypsdfghjklzxcvbnmQWRTYP` | letters only — the rule needs a digit **and** a letter, and this has the highest entropy of any vector tried |
| `…/v1/550e8400-e29b-41d4-a716-446655440000` | a UUID scores ~3.39, just under the 3.5 bar — and UUID-as-token is common |
| `…/v1/98274510923847561092837465` | digits only — same composition gate |
| `…/v1/hunter2hunter2x` | 15 characters, one under `_KEYISH_MIN_LENGTH` |

Widening it is a precision trade, not a bug fix: every loosening costs legitimate
routes. `api-` was in the prefix list and was **removed** for exactly that reason
— it refused `/api-v1/chat` and `/api-gateway/v1` while catching nothing the
entropy rule missed. Decide the trade when add-an-endpoint-by-prompting lands,
because that is the flow where a *model* starts composing these URLs.

### `read_web_page` + destination visibility (shipped alongside step 1)

A new SAFE-view tool, `agent_core/tools/read_web_page.py`. It was added on the owner's finding that a search whose snippet
lacks the answer left Addison with nothing to offer but "open this and read it
yourself", which is backwards for the personas. LOW, read-only, no `undo()`, in
`_V1_TOOL_IDS`, so the **Simple** profile gets it: reading a page and answering
from it is the companion's core job, not a developer affordance.

What it widens, stated plainly: it is the **first SAFE tool that sends a request
to an address the MODEL picks**. `web_search` reaches one fixed host; `open_link`
reaches anywhere but opens a visible browser tab. Every URL — the first and every
redirect hop — is vetted by **resolved IP** and the connection is **pinned** to the
address that was vetted (`_vet` / `_pinned_url`), so SSRF and DNS rebinding are
closed. What is not closed is *outward reach*: nothing here mutates anything, but
read-only is not the same as "cannot carry data outward".

The owner's answer (2026-07-20) is **visibility, not per-site grants**: the tool's
`permission_detail` names the site, and the Activity Panel renders it on every
granted call in both modes (`tool.activityUpdate` gains an optional `detail`).
Adding prompts was rejected — being asked too often is the complaint that started
this work.

**Open items, all real, none silently accepted:**

| Item | Why it is open | Where it lands |
|---|---|---|
| **Grant scoping is per tool id, not per site** | A SAFE grant is keyed by tool id, so after the first permission card every later read is ungated and model-addressed. Visibility is the mitigation that shipped; narrowing the grant to a site is a **permission-gate** change, not a tool change. | A gate change — size it before Phase-2 step 5 |
| **The panel names the REQUESTED host, not the reached one** | `on_activity` fires *before* `tool.execute`, so a 302 from `innocent.example` to `attacker.example` is announced as the former. Every hop is re-vetted, so nothing unsafe is *reached* — but the visibility guarantee has a redirect-shaped gap, and a model steered by page text is exactly the actor who would use one. Closing it means emitting after execution carrying `_Fetched.url`, i.e. a real orchestrator↔tool contract change. | Next tool-loop change; do not let it live only in a docstring |
| **The detail names the site, never the payload** | Deliberate — a full URL would put the query string, and anything a page hid in it, on screen and into any screenshot. The consequence is that an exfiltrating read of a *familiar* host looks exactly like an honest one. Real untrusted-content **screening** is the v2 item that covers this (design-doc §11), and this tool materially enlarges its surface: snippet-sized untrusted text becomes page-sized. | **v2**, with screening |

Bounded in this pass, so they are not open: the turn's tool loop is capped on
**both** axes — `_MAX_TOOL_ROUNDS` (chaining: a page ending "now read …/2") and
`_MAX_TOOL_CALLS` (fan-out: one response carrying hundreds of `tool_use` blocks).
The routine engine emits `on_activity` too, so a saved routine containing a page
read is not the one path where the destination goes unnamed.

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
- Commands: pytest as above · `ruff check agent_core tests` ·
  `npx --yes pyright` (repo root; config `pyrightconfig.json`) ·
  `cd shell && npm run lint && npx tsc --noEmit && npm test && npm run build` ·
  `cd shell/src-tauri && cargo test`.

## The live-driver pattern (cheap end-to-end tests)

Spawn `agent_core/.venv/bin/python -m agent_core.main` from repo root with
`ADDISON_DB_PATH` pointed at a tmp dir; a reader thread consumes stdout lines;
frames whose method starts with `shell.`/`keychain.` are answered BY THE DRIVER
(play the Rust shell: `keychain.getProviderKey` → `{"key": ""}` so the core falls
back to its env key; `shell.saveNewFile` → write in tmp, return `{path}`;
`shell.deleteFile` → delete within tmp only); `permission.requestGrant`
notifications are answered with `permission.respond {toolId, allow: true}`;
everything else is request/response by JSON-RPC id. Cap turns, use
`claude-haiku-4-5` via `ADDISON_MODEL`, per-request timeouts ~90s. This validated
the whole stack for pennies — reuse it.

## Known gaps (deliberate or tracked, not bugs)

**This is the single live-issue register.** Items that used to sit in the step-1
ledgers, the deferred-with-reason table and the 07-24 "where a bug could have
entered" list were folded in here when those sections were retired (2026-07-26);
everything still open is below, and the closed rows went to git.

**Still open from the retired step-1 ledgers:**

- **`tool_grants` capture is still undecided.** Excluded today, and correctly so —
  the table is inert (nothing reads or writes it; `PermissionGate` keeps grants in
  memory). If grants ever persist, restoring a snapshot taken *before* the user
  revoked one would **reinstate** it: a privilege grant delivered by a deliberately
  ungated one-action button. If it is ever captured it must be an **INTERSECT**,
  never a replace.
- **`LiveDatabaseBlocked` should probably be a `BaseException`.** It subclasses
  `AssertionError`, so a broad `except Exception` swallows it and
  `JsonRpcServer._rebuild_into` reports "rebuild failed" instead of naming the
  guard. The block still HOLDS — nothing is written; what is lost is the loud
  message, in the one place a loud message is the whole point. Changing it alters
  every existing handler's behaviour, so it needs its own verification pass.
- **`routines/engine.py` — the dev-only guard duplicates `on_failure` handling.**
  It shapes its refusal as a failed step and re-implements abort / ask_user / skip
  **inline** instead of falling through to the canonical `if not result.success:`
  block (~L255). It matches that block today and will silently diverge the moment
  someone adds a fourth `on_failure` policy. Fix by restructuring so both paths
  share one block; it was written this way to keep a diff small, which was the
  wrong trade for a branch nobody exercises often.

**Opened by steps 4 + 5 — decide these, don't rediscover them:**

- **The webview cannot open an external link, at all.** `main.rs` registers three
  commands for it (`send_to_core`, `store_provider_key`, `delete_provider_key`);
  `shell.openExternal` is CORE→shell, reachable only by the `open_link` tool, and
  `Markdown.tsx` states the rule as "the webview must never open URLs itself, and
  must never call any `shell.*` IPC method". So every address shown in Settings is
  copy-paste text (the Google free-tier line now says so honestly), and
  `Markdown.tsx`'s inert anchors are inert for the same reason. If clickable links
  are wanted, the fix is **one narrow webview→shell Tauri command**, not an anchor
  — and it is new highest-trust surface, so it is an owner call, not a cleanup.
- **The Custom guard panel still has no workspace-trust guard**, which CLAUDE.md
  and this file both said step 5 would add ("as those capabilities land, never
  before"). It was not in the frozen step-5 contract, so it was not built. In the
  meantime the precedence question is answered defensively: `auto_grant_scope='none'`
  now beats trust (see rigor-pass item 6). **Decide at step 6 or 8** whether the
  panel grows the third guard or whether that precedence rule is the whole answer.
- **`tsc --noEmit` does not cover the test files** — `tsconfig.json` excludes
  `src/__tests__` and `*.test.ts(x)`. A fixture that drifts from the hook signature
  it drives is invisible to the typechecker; that is exactly how `useTurn.test.tsx`
  came to be missing a required callback. Consider a second `tsconfig.test.json` in
  CI; it is a real hole in a gate people trust.
- **`policy._canonical` case-folds unconditionally**, so `/tmp/PROJECT/x` is judged
  inside the trusted root `/tmp/project`. Correct on APFS/HFS+ default
  (case-insensitive), **wrong on a case-sensitive volume**, where it widens
  confinement. macOS-only assumption, currently undocumented in the function.
- **The floor protects Addison's DATA, not Addison's CODE.** A trusted root may
  contain the repo (fine — that IS the harness working for a developer) or, in a
  packaged install, `/Applications/Addison.app`, where the model could rewrite
  `policy.py` card-free. The amendment's "inviolable machinery: Addison's code and
  the global floors" is therefore broader than what ships. Either narrow the wording
  or add the running app's resource root to `_protected_dirs`. **Owner call.**
- **A hardlink inside a trusted root to a file outside it is trusted** — `realpath`
  cannot see hardlinks. Inherent to any realpath-based confinement; noted rather
  than fixed.
- **`workspace.pickDirectory` blocks the worker thread** on a modal dialog with the
  bridge's 60s ceiling; browse for longer and the timeout is swallowed into
  `{"directory": null}` with no explanation, while every other store RPC queues
  behind the open dialog.
- **A failed endpoint add still clobbers the keychain**: the card stores the key
  under `custom` before `confirmAddEndpoint`, so a failed connect leaves the new key
  overwriting any previous custom-server key, with no rollback and no disclosure.
  The ordering is contract-mandated and G1 is intact; the undisclosed clobber is not.

- `draft_message` compose handoff: Rust returns "not available yet" — a real
  discardable-draft mechanism is required by the undo invariant.
- No file-attach/drop UI → `read_file` unreachable from chat.
- Setup Assistant relay is client-complete; the server side is external by design.
- Packaging/signing/updater = Phase 3.
- **`primary.txt` widget guidance says Addison can't build custom-app widgets.**
  True of the code today, and #45 deliberately strengthened it after a live
  false-success failure — but wrong as a statement of the amendment's intent.
  Rewrite capability-aware in Phase-2 step 6, when to-do/note/timer widgets
  actually exist. A prompt-only guard is mitigation, not a fix: it has now
  failed once (#43) and been re-hardened once (#45). If it regresses a third
  time, go structural — a registry-level guard on `save_file` calls that look
  like widget substitutes.
- **The design-doc and engineering-spec *bodies* predate the SAFE/OPEN
  mode-scoped model and have no widgets section.** They carry amendment banners
  and precedence notes, but a dedicated reconciliation pass would be worthwhile.

## Working conventions (established with the user)

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

## Tracked thread: macOS keychain prompts

**STATUS 2026-07-25: root causes confirmed and FIXED. Merged as PR #57**
(`fix/keychain-double-prompt`, commit `5e435dd`). PR #58 and everything after it
sits on top of this.
The unexplained multi-prompt symptom was diagnosed by a multi-agent audit with
adversarial verification and fixed the same day. What it was, in one breath: the
device identity had no session cache (two OS reads per relay message —
`getDeviceKey` + `signRelayRequest`); a denied/failed provider-key read was
indistinguishable from "no key saved", so the core silently rerouted the turn to
the external Setup Assistant relay (which then raised the two device-identity
dialogs); and `keychain::handle` ran synchronously inside the stdout pump, so one
open dialog stalled every core↔shell call into the 60s bridge timeout. The owner's
"six prompts + app restarting on its own" episode was the dev watcher: agents
editing `keychain.rs` while `tauri dev` ran meant a rebuild+relaunch (fresh ad-hoc
signature, empty session cache) per save.

Shipped (all suites green: cargo 64, pytest 879, ruff, pyright):

- **Device-identity session cache** (`DEVICE_CACHE`, keychain.rs) — same owner
  decision as KEY_CACHE; one OS read per launch; corrupt blobs never cached.
- **The three-way key-read seam** — `{"key": "<value>"}` / `{"key": ""}` (nothing
  saved — a normal result now, NOT an error) / app error "Couldn't read your saved
  key from the keychain." Core-side: `_primary_key_status()` = ready | missing |
  unreadable (`rpc/constants.py`); **unreadable answers the turn on-machine with
  `_KEY_UNREADABLE_MESSAGE` in BOTH profiles — a keychain failure can no longer
  send a message to the relay** (pinned by `tests/test_keychain_read_failures.py`
  with a recording relay stub).
- **Negative failure cache** (`FAILED_READS`, keychain.rs) — a denied read is
  remembered for the session (stops the 60s stats-poll dialog storm); evicted on
  store/delete (re-saving or removing the key is the retry signal).
- **Pump unblocked** (`spawn_keychain_request`, agent_process.rs) — keychain work
  runs on a blocking task; OS access serialized by one `OS_KEYCHAIN` mutex (also
  taken by store/delete, which closes the parked-read stale-cache race);
  `_KEYCHAIN_TIMEOUT = 600s` core-side for the three keychain methods.
- **Migration ordering** — legacy entry deleted only after the copy lands
  (`migrate_legacy_key`, closure-testable); **Remove now also deletes the legacy
  `provider-key:primary`** so a removed key cannot resurrect (the orphan measured
  on the owner's machine was the live resurrection source).
- **provider.connect** reads the key up front so a read failure surfaces the
  keychain sentence, not "That key doesn't work."

**Round-2 regression fixes (2026-07-25, same working tree, all suites green:
cargo 66, pytest 880, ruff, tsc, clippy).** A second adversarial audit of the
round-1 diff found five real regressions it had introduced; all fixed:

- **Sync keychain Tauri commands froze the whole window.** `store_provider_key`
  / `delete_provider_key` took the new `OS_KEYCHAIN` mutex on the main thread, so
  a core-initiated read parked at a password dialog would beachball the UI. Both
  are now `async` + `spawn_blocking` (keychain.rs).
- **Frontend 120s timeout vs the core's 600s keychain wait.** `sendMessage` now
  uses `TURN_TIMEOUT_MS = 900_000` (client.ts) so the reply the person waited for
  behind a dialog isn't dropped and the composer can't invite a duplicate turn.
- **Anthropic-only probe rerouted OpenAI/Google/custom-only users to the relay.**
  `_run_send_message` now treats a connected non-Anthropic provider row as
  standing evidence of a PRIMARY-capable setup (`_other_cloud_provider_connected`,
  rpc/conversation.py) — no wrongful off-machine detour, no false BYOK demand.
- **Detached keychain task could answer a respawned core's colliding id.** The
  core channel now carries a `generation` counter (agent_process.rs); a keychain
  task writes back only while its captured generation still matches, else drops.
- **`delete_provider_key` evicted the cache before taking the OS lock**, so a
  dialog-parked read could resurrect the removed key. It now evicts again *under*
  the lock.
- Also: a per-turn `fresh` read (`get_provider_key(provider, fresh)`, keychain.rs;
  `_primary_key_turn_probe`, main.py) lets a person's own next message retry past
  a dismissed dialog, while the launch/poll probes stay quiet — the negative
  cache no longer strands a user who dismissed once by mistake.

Still open (all LOW severity, from the round-2 audit — verify before acting, the
verifier agents were cut off by a session limit): `shell.pickDirectory` still
keeps the 60s budget though it waits on a person; connect-card vs chat-turn use
two different "unreadable" sentences; `_KEY_UNREADABLE_MESSAGE` names a permission
prompt that (for the automatic pollers) the negative cache suppresses; the
`answeredWith routed` flag mislabels an explicitly-chosen Local turn as "answered
with a free model"; net-vetting gaps (`read_web_page` omits `total_timeout`; the
custom-provider CHAT `send()` re-resolves its base_url unpinned; connect-validation
GET reads the body with no size cap); snapshot-floor edges (`restore_last_working`
gives up on first apply failure unlike the sidecar arm; a G4 anchor duplicating
the newest verified fingerprint can defeat the newest-two-verified prune
exemption; `_sweep_sidecars` can erase prior sidecar history once a fresh DB has
one genesis row); `undo_manager.record()` sits outside the per-call error
envelope; `_canonical`'s unconditional casefold can merge distinct dirs on a
case-sensitive volume (widens workspace trust); the file tools' permission card
names the raw arg basename, not the resolved path. A failed *device-identity*
read is still not negative-cached (aborts the turn visibly, no storm — deliberate);
the wall-clock upper-bound assert in `test_shell_bridge.py` may flake under load;
signing-script automation is still un-agreed (ask before imposing the split dev
loop).

**STATUS 2026-07-24: step 1 of the plan is DONE and working.**
`scripts/sign-dev-binary.sh` signs the dev binary with a stable self-signed
certificate. Verified on the owner's machine — the designated requirement the
keychain ACL matches on went from a per-build hash to:

```
designated => identifier addison and certificate leaf = H"c24af4b8…"
```

Two things the next session needs to know:

- **The certificate must be TRUSTED, not just created.** A self-signed root from
  Certificate Assistant is `CSSMERR_TP_NOT_TRUSTED` until you open it in Keychain
  Access → Trust → set **Code Signing** to **Always Trust**. Until then
  `security find-identity -v -p codesigning` reports **0 valid identities** and the
  script correctly refuses. This step was missing from the first version of the
  instructions and is where the owner got stuck; the script now detects that exact
  state and says so. (That fix merged as PR #50.)
- **`cargo` strips the signature on every rebuild**, so `./scripts/sign-dev-binary.sh`
  must be re-run after each build. This is a step someone will forget. Wiring it
  into the dev loop was offered and **not** done, because `tauri dev` builds and
  runs in one step with no hook between, so automating it means running Vite and
  the binary separately — a workflow change the owner has not agreed to. Ask before
  imposing it.

**The multi-prompt symptom was the unexplained one, and the 07-25 audit above
explained it.** The owner reported **three prompts in a single launch** (one
process, confirmed by `ps`). Signing explained prompts *across* rebuilds but not
three within one process, because `KEY_CACHE` should collapse provider-key reads
to one. The hypothesis recorded here was that a failing `provider-key:anthropic`
read fell through to the Setup Assistant relay, which then read `device-identity`
twice with no cache — three prompts naming **three different items**. That is
what the audit found and fixed: the three-way key-read seam (a failed read is now
distinguishable from "nothing saved" and answers on-machine instead of routing to
the relay), `DEVICE_CACHE`, and the orphaned legacy entry as the resurrection
source. **The diagnostic stays cheap if it ever recurs: macOS names the item in
the dialog.** Same name three times = three launches, benign. Three different
names = the cascade is back.

Original diagnosis below. Cause 1 is unchanged; cause 2 has since been fixed
(`DEVICE_CACHE`) and is kept for the reasoning.

**1. Dev builds are ad-hoc signed, so the ACL is invalidated on every rebuild.**
`codesign -dv` on `shell/src-tauri/target/debug/addison` reports
`Signature=adhoc`, `TeamIdentifier=not set`, and an identifier carrying a
per-build hash (`addison-<hash>`, not the `app.addison.desktop` in
`tauri.conf.json`). macOS keys the "Always Allow" keychain ACL to the signing
identity, so **every rebuild presents itself as a different application** and the
ACL is discarded. Clicking Always Allow in development is therefore not sticky,
and never can be while the signature is ad-hoc.

**2. `ensure_device_keypair()` is not covered by `KEY_CACHE`. — FIXED 2026-07-25**
by `DEVICE_CACHE` (keychain.rs), one OS read per launch. `KEY_CACHE`
(`keychain.rs`) caches *provider* keys, and `get_provider_key` consults it first —
one OS read per provider per launch. `ensure_device_keypair` calls
`entry.get_password()` directly with no cache, so the Setup Assistant relay path
does **one OS keychain read per message**. On a build whose ACL keeps being
invalidated, that is one prompt per message.

**Agreed plan, in order — do not skip to the end:**

1. **A stable self-signed development certificate.** Fixes the actual cause: a
   stable signing identity means the ACL survives rebuilds and Always Allow
   works. Free. *The $99 Apple Developer Program is for distribution — signing,
   notarisation, shipping to other people's machines. It is a Phase-3 packaging
   concern and buying it now would not fix this.*
2. **Cache the parsed `SigningKey`** in the same shape as `KEY_CACHE`, only if
   prompts persist after (1). Deliberately second: it is a workaround for
   per-message reads, and it widens what sits in process memory, so it should not
   be spent on a problem step 1 may have already solved.
3. **Secure-Enclave-backed device identity — Phase 3.** Note the constraint
   before planning around it: the Secure Enclave is **ECDSA P-256 only, not
   ed25519**. Today's identity is ed25519 (`ed25519_dalek`, deterministic per RFC
   8032), so this **changes the relay signing contract** on both ends. It is a
   protocol change, not a storage change.

**Also found: an orphaned legacy keychain entry.** `get_provider_key` migrates
`provider-key:primary` into `provider-key:anthropic` and best-effort deletes the
legacy entry — but only on a read that finds **no** per-provider entry. Once
`provider-key:anthropic` exists, the read returns early and the legacy account is
never revisited. So a legacy entry orphans permanently whenever the migration's
best-effort delete failed, or whenever the user saved an Anthropic key under the
new scheme before any read triggered the migration. `delete_provider_key`
("Remove") then deleted only the per-provider account, so a stale key sat in the
user's keychain after they believed they had deleted it — and the next read
resurrected it through the migration fallback. **FIXED 2026-07-25:**
`delete_provider_key_blocking` now deletes `provider-key:primary` as well when
the provider is the legacy one; a missing legacy entry counts as success, a real
failure is reported because the key genuinely is still on the machine, and the
retry is idempotent.
