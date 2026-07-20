# Addison — Session Handoff (2026-07-20, second pass)

For the next working session. Read **CLAUDE.md** first (repo law), then
**`docs/addison-scope-amendment-2026-07.md`** (the adopted scope shift — it
governs where it and the older specs disagree, *except* where an inline
owner-decision note supersedes it), then this.

**Next up: Phase-2 step 2 — the Custom profile + guard model + the G4 anchor
minting caller.** Step 1 (the snapshot/rollback floor, G3) is **done**. Details
at the bottom, including a ledger of everything step 1 deliberately left behind.

## Where the project stands

- The whole v1 build order (engineering-spec §11, steps 1–11) is implemented and
  **merged to `master`**. No open PRs, no stacked chain — the stacked-PR era is
  over; every change now goes PR → `master` directly.
- **A major scope amendment was adopted 2026-07-20.** Phase 1 (docs) landed;
  **Phase-2 step 1 (the snapshot/restore subsystem, floor G3) is now built.**
  Addison is a **butler**: Developer = a Claude-Code-class **coding harness**;
  Simple = an all-in-one **companion**; a new **Custom** profile tunes prompting
  guards. Safety is redefined as **guaranteed rollback**, and as of this session
  that redefinition has code and tests behind it.
- **Gates, all green:** **520+ pytest + 1 xfail** (was 385; the final closing
  fixes were still landing tests as this was written — run the suite, don't trust
  the number), **pyright 0 errors**
  (repo-wide; the remaining diagnostics are `reportMissingImports` for
  `pytest`/`httpx`, pre-existing — pyright has no venv), ruff clean, **65 vitest**
  (was 48), ESLint clean, `tsc --noEmit` + `vite build` clean, **31 Rust tests**
  (was 30). **Green gates are not the bar — see "How step 1 was verified" below.**
- **CI exists** — `.github/workflows/ci.yml`, three jobs (python:
  ruff·pyright·pytest / frontend: eslint·tsc·vitest·build / rust: cargo test) on
  every PR and push to `master`. Keep it green.

## What shipped this session — Phase-2 step 1, the G3 rollback floor

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

**The manager.** `agent_core/snapshots/snapshot_manager.py` (920 lines) —
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

**Frontend.** The Settings **"Restore points"** card (never called "Snapshots" in
any user-facing string), placed directly under Profile. Fern-filled restore button
(never the rose `danger` token — a recovery is not a destruction), a two-step
inline confirm carrying the consequence copy plus a profile-change sentence and a
genesis sentence when they apply, the target always named with its timestamp
before the button, and the blocky **Permanent** tag with no Remove control on
anchors. QA steps: **TESTING-CHECKLIST §13a**.

**Two decisions worth internalising before you extend it:**

- **Restore is an RPC path, never a registry tool, and never passes the permission
  gate.** A gate that could deny a restore would make "the restore path is itself
  unbreakable" false. The only model-facing snapshot surface that will ever exist
  is a **LOW, capture-only** `snapshot_now` tool — add a row, nothing else.
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

## What shipped since the last handoff (07-17 → 07-20)

- **Fern UI wave + mode-scoped safety backend** (#30, #31).
- **Maintainability pass** (#32): SQLite indexes, `usage_log` retention pruning,
  shared IPC test fixtures (`tests/conftest.py`), the Vitest rig.
- **Conventions hardening** (#33): the first CI workflow; repo-wide pyright
  clean; ESLint flat config (react-hooks + a `lib/parse.ts` import guard); an AST
  **module-boundary test**; **payload-shape drift fixtures** (Python generates
  real core payloads, vitest parses the same files); one conservative provider
  retry; WAL + busy_timeout; the `Tool`/`UndoableTool`/`RedoableTool` protocol
  split; **`main.py` decomposed** 2,318 → 1,279 lines into `agent_core/rpc/`
  mixins + a dispatch table.
- **Frontend UX**: system-following theme (light/dark/**system**) + no-jump
  interactions + calm animations; Settings uses the ☰ drawer idiom (#35);
  sidebar always present on desktop (#36); mobile bell removed and **widgets
  moved inline into the chat screen** (#37, #39); drawer close-arrow + slide-out
  animation on every close path (#38); **app icon is now the bell** (#40);
  **rename chats by double-clicking the sidebar title** (#42).
- **Skills** (#41): user-authored **declarative guidance notes** appended to the
  transient per-turn system prompt. They can *steer* but never widen what Addison
  may do — the registry + gate stay the sole authority. Plus two seeded in-house
  stat widgets.
- **`primary.txt` widget guidance fix** (#43), **hardened again in #45** after a
  live failure: asked to "build me a widget that works as a to-do app", Addison
  wrote `todo-widget.html` via `save_file` and reported "Done!" — nothing reached
  the rail, because no widget-creating tool exists. #45 moved the prohibition out
  of a buried bullet into two override rules (no file as a stand-in; no "Done"
  for something that doesn't exist). ⚠️ Interim-correct only: it describes what
  the code can do **today**. Phase-2 step 6 must make it **capability-aware** and
  will undo this wording.
- **Docs: scope amendment adopted across all authoritative docs** (#44).
  `addison-design-doc.md` + `addison-engineering-spec.md` were **un-gitignored
  and are now tracked** in the repo.

## The scope amendment in one screen (read the full doc)

- **Identity** — butler. Developer = coding harness + Addison's safety/QoL;
  Simple = companion; Custom = tunable *prompting* guards (deep in Settings,
  behind extra confirmation).
- **Four global floors, never off in any mode:**
  - **G1** — API keys keychain-only, never webview/SQLite/snapshots.
  - **G2** — Addison **never triggers itself**. It *may author* automation the OS
    runs (cron/launchd/watcher); running or arming a powerful action requires a
    **user-typed keyword prefix** (e.g. `!run …`) — which, being user-typed, is
    also a prompt-injection barrier.
  - **G3** — **guaranteed rollback**: app-state snapshots (automatic before risky
    changes **and** on command), keys excluded, restore to the last
    *verified-working* state, restore path itself unbreakable. **Built.**
  - **G4** — turning a guard OFF in Custom mode mints an **undeletable anchor**
    that **records the app build it was minted on**. (G4 ≡ what the older docs call
    "the undeletable-anchor rule"; use **G4** in code, comments and test names.
    **Owner decision 2026-07-20:** this replaces the earlier "also captures the app
    binary" wording. What ships is a build *reference*; **restoring a binary is not
    implemented** and is a Phase-3 updater item. The repo must not carry a floor its
    own tests do not cover — that is the anti-pattern the amendment was written
    against, so the promise was narrowed to what the code does. The correction is
    applied in `CLAUDE.md`, the amendment §3.1/§3.3/§12/§13 Q8/§14, the design doc,
    the engineering spec, `architecture.md`, `data-model.md` and `classes.md`.)
- **Reversible data/config vs. inviolable machinery** — the user *and* the model
  may change endpoints, models, guards, skills, widgets, routines, because all of
  it is snapshotted and one-action reversible. Addison's code and the floors are
  never alterable.
- **Widgets are buildable in ALL modes, capability-gated** — SAFE = a safe,
  **non-destructive** vocabulary (launchers + interactive kinds: to-do/checklist,
  note, timer) via trusted renderers and safe storage, no arbitrary code;
  higher tiers add **code-backed / system-capable** widgets under
  workspace-trust + undo + snapshot + keyword gate.
- **MCP client** (consume external tools through the existing registry + gate),
  never a server/gateway. **Routing:** 4 strategies (quality-first default,
  cost-first, local-only, balanced) + Developer custom. **Free models:** Addison
  must be useful without a paid frontier key; legit free/local only in-app,
  gray-area routers documented on GitHub only. **"Make it cheaper"** = a
  previewed skill + model change, auto-snapshotted, one-click undo.

## Next up — Phase 2 (code), in dependency order

1. ~~**Snapshot/restore subsystem (G3)**~~ — **DONE** (see the section above).
2. **Custom profile + guard model + undeletable anchor** (`policy.py`) — **start
   here.** Step 1 left it four things that are already built and waiting:
   `mint_anchor()` (fully implemented and tested, **no caller** — the Custom guard
   toggle is the caller); the `custom` value already admitted by
   `created_in_mode`'s CHECK constraint, so no migration is needed; the
   `guard_weakened` reason slug, reserved and unwritten; and
   `ipc.restoreSnapshot`, a typed targeted-restore wrapper with no caller, for the
   anchor path. Also land the **`snapshot_now` tool** here: **LOW and
   capture-only** — it may only ever ADD a row, never restore and never delete.
   Mind the ordering trap that caused it to be deferred: `build_registry()` runs
   inside `main()` *before* the Store (and so the SnapshotManager) exists, so the
   tool must close over a late-bound `Callable[[], SnapshotManager | None]` and
   answer "I can't save a restore point just yet" when the store isn't up.
3. **Routing strategies** (4 + custom) + companion prefer-quality/prefer-free
   toggle + free-model disclaimer + graceful fallback/cooldown.
4. **Free-model endpoints** — legit free/local + add-an-endpoint-by-prompting.
5. **Harness + workspace-trust** (OPEN): grant a project dir; inside it the gate
   still runs and logs but doesn't prompt; outside, unchanged.
6. **Widget capability tiers + expanded safe vocabulary**; make `primary.txt`
   capability-aware.
7. **MCP client** tools through the registry + gate (SAFE: read-only/undo-able
   only — invariant 2 enforces it).
8. **Automation keyword gate** + author-OS-run automation.

Steps 3–4 (companion-facing) can run in parallel with 5–8 once 1–2 land.
Close the amendment's **§13 open questions** as you go. **Three are now closed —
do not relitigate them**, the reasoning is recorded inline in §13 and summarised
below. Still open: keyword syntax (Q1), Custom reachability (Q3), auto-routing
depth (Q5), MCP-in-SAFE constraint (Q6), widget capability declaration (Q7).

### Resolved §13 questions (decided, with reasons — don't reopen)

- **Q2, retention.** Keep **50 snapshots or 30 days, whichever keeps more** — the
  same idiom as the undo window, so there is one retention concept in the
  codebase — with two exemptions written **into the SQL**, not left to a caller:
  permanent rows, and **the newest verified-working row**. Retention here is not
  housekeeping; a rule that can prune the last verified row leaves the one-action
  restore with no target, i.e. G3 silently off with no error anywhere — the
  friend's failure reintroduced by the recovery machinery itself. **Anchors never
  prune and never count against the budget.** The amendment's alternative ("the
  single most-recent working anchor") was rejected because it needs to *replace*
  an undeletable row, creating the codebase's only `DELETE … WHERE undeletable =
  1` — the exact statement G4 says must not exist. Its worry was storage, and Q8
  removes it: an anchor is a few KB.
- **Q4, verified-working.** **Any turn whose response was sent** — execution
  reached `_respond({"ok": True, …})` in `rpc/conversation.py`. A tool failure is
  deliberately *not* a turn failure. The "no rolled-back action" variant was
  rejected: it couples config health to file-level regret through an independent
  mechanism with an unbounded window. The mark does **not** flag the pre-change
  row (that config never ran) — it captures the **current** config as a new
  verified row, deduped by fingerprint.
  **Honest residual, and the next session should know it was a deliberate
  trade:** "a turn completed" is satisfied by configs that are *degraded* rather
  than dead — which is the whole "make it cheaper" class, i.e. the friend's own
  case. The mitigation is that `restore_last_working()` never targets a config
  identical to the present one, so **each click steps back one distinct proven
  configuration**. But **two bad changes deep, the user clicks Restore twice**: one
  click lands on the *first* bad config, not the last good one. That is bounded
  (the next click goes further), visible (the card names the target before you
  click), and was chosen over a stronger predicate that would have to observe the
  future — `mark_verified_working()` fires on every successful turn and must stay
  cheap, idempotent and non-raising. If this ever needs improving, the honest fix
  is episode tracking, not a cleverer local rule. *(Related: the **genesis** row is
  written `verified_working = 1` before any turn has run. Strictly nothing proved
  it — but G3 requires a restore target to exist at all times, including during
  onboarding, and refusing the mark leaves both G3 and G4 unsatisfiable in that
  window.)*
- **Q8, anchor binary capture.** **A version pin, and capture only.** `binary_ref`
  holds `{"version", "identifier"}` from `shell.appBuildRef` — never bytes, never a
  path (an earlier draft carried the executable path; dropped, because nothing read
  it, it goes stale on any move, and it would write the user's account name into a
  plaintext sidecar and into every permanent anchor). Copy-on-write was rejected as
  platform-dependent — APFS `clonefile` degrades silently to a full copy across
  volumes, making an anchor's size depend on the user's disk layout, which is not
  something a floor should rest on. **Binary restore does not ship** and is a
  Phase-3 updater item; see the G4 note above for why the docs were corrected
  rather than the promise left standing.

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
- **The sidecar arm is gated on the walk having started at the top**
  (`sidecars_ok`). The sidecar path has no notion of walk position, so letting it
  run after the walk deliberately stepped past newer rows would re-apply one of
  them — the recovery path undoing the user's own rollback.

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

### Deferred with reason — the step-1 ledger

Nothing here is forgotten; each line names where it lands and why it waited.

| Item | Why it waited | Where it lands |
|---|---|---|
| **Anchor minting caller** | The Custom profile doesn't exist — `ProfileId` has two members. `mint_anchor()` is built and tested. | **Step 2** |
| **`snapshot_now` tool** | Solvable now behind a late-bound callable; deferred only to keep step 1's blast radius small, since everything leans on it. Owner ruled: step 2. | **Step 2**, as **LOW and capture-only** — may only ever add a row |
| **`tool_grants` capture** | Excluded, and correctly so. The table is inert today (nothing reads or writes it; `PermissionGate` keeps grants in memory). More important: once grants persist, restoring a snapshot taken *before* the user revoked a grant would **reinstate** it — a privilege grant delivered by a deliberately ungated one-action button with no permission card in the path. A floor must not be a privilege-escalation vector. | **Step 2**, if grants ever persist — and then as an **INTERSECT**, never a replace |
| **Data-dir permanent distrust** | Workspace-trust doesn't exist until step 5, but the rule must be fixed *before* it does, or `run_command` inside a trusted parent directory can `rm -rf` the floor's own storage with no card. | **Step 5**. Write `test_the_addison_data_dir_can_never_be_workspace_trusted` **now, as an `xfail`**, so the rule exists before the capability does |
| **`_valid_http_url` credential hardening** | **Pulled forward from step 4 and landed in step 1** — see the G1 note below. A `base_url` carrying a secret lands in a plaintext sidecar *forever* via any permanent row, so it could not wait. Userinfo, any query string or fragment, and key-shaped path segments are now refused by `_base_url_problem` at the moment the person types the address, not stripped on capture — stripping would make a restore write back a *different* address and silently break their server. | **Landed (step 1).** Residual: the path check's composition gate and entropy bar let some token shapes through (a UUID, an all-letter or all-digit segment) — see the G1 note above |
| **Fresh-vs-upgraded install flag** | `_looks_like_a_fresh_install` *infers* which install this is from the config row-image, because the module may not import anything or read a setting. It is a heuristic, and a deliberately biased one. A one-line first-run flag written by `main.py` (which knows the answer for certain, and may read whatever it likes) and passed to the manager as a constructor argument would replace inference with fact. | **Step 2**, alongside the other `main.py` wiring — cheap, and it retires a guess from the floor |
| **Binary restore** | Owner-descoped — collides with the unwired `updater.rs`, and would be the one piece of the recovery floor that could itself brick the app. | **Phase 3**, as an updater work item |
| **`mcp_servers` / workspace-trust capture** | The tables don't exist. `test_capture_scope_covers_every_schema_table` forces the decision the moment they land. | **Steps 5 and 7** |
| **Routing-strategy + "make it cheaper" + add-endpoint hooks** | Those flows don't exist yet; the `reason` slugs are already reserved in `REASONS` so the vocabulary won't churn. | **Steps 3 and 4** |
| **"Reset Addison" reconciliation** | Design-doc §9 describes a pre-amendment "Reset Addison" control that "clears corrupted app state". Read literally that could delete anchors. **Whatever Reset ends up doing, it must never be able to delete an anchor** — and the database triggers currently make that a hard failure rather than a silent one, which is the right way round but will surface as an error someone has to design for. | **Flag to the owner in the next doc pass**; decide before Reset is implemented |

## Environment facts

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
- `shell/src/components/BottomSheet.tsx` is orphaned (unused since widgets moved
  inline on mobile) — delete or repurpose.
- **Three loose ends left by step 1, all deliberate, all small:**
  - `RestoreResult.providers_needing_a_key` **has no writer**. The keychain probe
    lives in `rpc/snapshots.py` (the manager may not touch the keychain, by
    design) and it computes the names itself rather than filling the field. Either
    drop the field in step 2 or give it a writer — don't leave it looking populated.
  - `ipc.restoreSnapshot` **has no caller.** The Restore points card offers save /
    restore-last-working / per-row remove, but no per-row *restore*, because
    §1.1's card spec didn't list one. It is typed and waiting for step 2's anchor
    path. If a list of restore points that offers no way to pick one reads wrong in
    QA, it is a one-line card change.
  - **No cross-language test pins the genesis label.** `REASONS["genesis"]` in
    `snapshot_manager.py` and `GENESIS_LABEL` in `shell/src/ipc/client.ts` must
    stay equal — the card appends its "this clears everything" sentence by
    comparing them — but nothing enforces it. One line in `tests/ipc_fixtures.py`
    or the drift test would close it.

## Working conventions (established with the user)

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
- **Binding UI direction — the "Fern" redesign.** `docs/design-brief-fern/` is
  authoritative for tokens, type, shape, copy. Warm paper neutrals + one
  fern-green accent; serif message body (Source Serif 4), Public Sans UI, IBM
  Plex Mono for machine facts; **blocky = live annotation, rounded =
  ownable/actionable**; light default + class-driven dark, now with a
  **three-way Light/Dark/Match-this-computer** setting. Plain language for
  personas 54/68; never AI tropes or vendor branding.
- Verify UI changes in the browser preview where possible; note that the
  disconnected preview can't exercise the live core (no conversations, no skill
  persistence) — cover those with unit/component tests instead.
- The user starts every assistant message check with "Ad Astra." (memory).

## Tracked thread: macOS keychain prompts

Not a step-1 item and not a bug in the floor — a separate thread, opened this
session, with a plan the owner has agreed. Two independent causes were confirmed
against the tree.

**1. Dev builds are ad-hoc signed, so the ACL is invalidated on every rebuild.**
`codesign -dv` on `shell/src-tauri/target/debug/addison` reports
`Signature=adhoc`, `TeamIdentifier=not set`, and an identifier carrying a
per-build hash (`addison-<hash>`, not the `app.addison.desktop` in
`tauri.conf.json`). macOS keys the "Always Allow" keychain ACL to the signing
identity, so **every rebuild presents itself as a different application** and the
ACL is discarded. Clicking Always Allow in development is therefore not sticky,
and never can be while the signature is ad-hoc.

**2. `ensure_device_keypair()` is not covered by `KEY_CACHE`.** `KEY_CACHE`
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
("Remove") deletes only the per-provider account, so **Remove does not remove it
either** — a stale key can sit in the user's keychain after they believe they
deleted it. Small, but it is a G1-adjacent surprise and worth a targeted
best-effort cleanup of `provider-key:primary` in `delete_provider_key`.
