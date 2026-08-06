# Build log — what each step shipped, and what its rigor pass found

**This file owns the per-step record.** Read the entry for a subsystem before
changing it: the "what shipped" halves are largely superseded by `ROADMAP.md` and
git, but **the post-build rigor passes are where the real defects were**, and every
one of them describes a way this code has actually been wrong.

Not a changelog. Entries stop being added once a subsystem is stable; what earns a
place here is a finding a future session would otherwise rediscover the hard way.

*(Extracted from `HANDOFF.md` on 2026-07-27, unchanged.)*

---

## What shipped 08-06 — presence left the keychain, and every write heals

Steps 1 and 2 of [secrets-and-keychain-plan.md](secrets-and-keychain-plan.md), which
owns the design; this entry records what building them taught. Step 1: presence is a
column (`provider_config.secret_presence`, three-way, `present | absent | unknown`)
and no polled path asks the OS. Step 2: every credential write in `keychain.rs` is an
explicit, verified delete-then-add, foreign items self-heal after a successful read,
and an unchanged Save writes nothing.

**§14 decision 6 — the one-click experiment — is ANSWERED, and it voids a paragraph
of the plan.** Press *Always Allow* once, rebuild, relaunch: **no dialog.** Verified
this session with a genuine recompile through `npm run tauri dev`, and confirmed by
the owner. The plan's §4.2 "honest limit" said self-heal could only reset the clock to
the next rebuild on dev builds, because spike 1 (07-31) had measured an app-created
item prompting after a rebuild under the same certificate. What changed is not the
keychain, it is the signature: `sign-and-run.sh` now passes an explicit designated
requirement (`identifier "addison" and certificate leaf H"<cert>"`) instead of letting
`codesign` fall back to a per-build `cdhash`, so the rebuilt binary presents a
byte-identical requirement and the granted ACL still matches. **Self-heal is "once
ever" on dev builds too**, and spike 1's conclusion is now history. Its 29 ms
app-owned read is not history — it is the number the foreign-item threshold is
calibrated against.

What is worth carrying forward from building it:

- **`connected` could not carry presence, and the plan's §4.1 assumed it would.**
  "Did the validating request pass?" and "is a key saved?" are different facts, and a
  key that saved fine and was then REJECTED is the case that separates them. Folding
  them would have made a revoked key indistinguishable from no key — the exact
  collapse the three-way rule exists to prevent, one column over. Hence a new column.
- **The relay rule lives in ONE function, over three values, and the test asserts all
  three.** `may_reach_setup_relay` is true of ABSENT and nothing else. The plausible
  wrong version is `not present`, which is the 07-25 bug re-derived at a call site;
  it passes a two-value test and dies on the three-value one.
- **A "no row" is ABSENT, not UNKNOWN, and that needed arguing.** UNKNOWN is reserved
  for what the plan names: a read that FAILED, and a row predating the column. A
  provider Addison has never recorded a key for is a *recorded* state — it is the
  claim `provider.list` has always made by rendering it as not connected. Making it
  UNKNOWN would have meant a fresh keyless user attempting a live catalog fetch on
  every `availableRoles`, which trades an OS read for a network request.
- **The legacy implicit-connected fallback survived by being written down instead of
  re-asked.** `provider.list` used to render "a key is in the keychain with no
  connection row" as connected, computed by probing the OS. `record_secret_presence`
  persists the same answer the first time the per-turn read proves it.
- **A probe that RAISES is swallowed, so an OS-touch test must COUNT.** The first
  version of `presence_is_answered_without_touching_the_os` used a probe that raised
  `AssertionError` — and every honest presence caller wraps its probe in
  `except Exception`, because an unreadable keychain must not be a stack trace. The
  mutation that re-added the poll SURVIVED. A counter cannot be caught. This is the
  general shape: **never assert by raising through code whose job is to swallow.**
- **§5.4 cancels §4.2 unless it is qualified.** "Skip the write when the value is
  unchanged" would short-circuit the person whose item is foreign out of the very
  repair they need — pressing Save with nothing happening, which is the original
  reported symptom. The skip therefore also requires that the item is not foreign and
  that no repair has already lost the key.
- **Foreignness is detectable without a new API.** A foreign item always prompts, and
  a prompt always waits on a human — so a *successful read that waited* is a read of a
  foreign item. 400 ms, against a measured 29 ms app-owned read. One-sided: a slow but
  owned read costs one unnecessary (and verified) re-creation; a foreign read cannot
  slip under the bar without a dialog nobody saw.
- **The migration writes an item, and it must not then be healed.** The legacy
  migration spends its time on the LEGACY item's dialog while creating a fresh
  destination item under this build's identity. Elapsed time alone would have fired,
  and Addison would have run the one data-losing operation against an item it minted
  seconds earlier. Hence `OsRead.freshly_written`.
- **Delete-then-add needs a fourth read outcome.** If the re-add cannot be made to
  stick, the item is gone — and a later read finding nothing must not answer "no key
  saved", which is the cue to onboard. `KeyRead::LostInRepair` carries a plain
  sentence naming the only fix. `NothingSaved` stays a normal result; the two never
  merge.
- **What no test can reach, said out loud.** `get_provider_key`'s body needs a real OS
  keychain, so two properties are pinned by source-level backstops instead (the save
  path never calling `set_password`, the legacy delete gated on a verified copy), and
  one — caching the key *before* healing, so a torn repair cannot cost the current
  session — survived its mutation and is guarded by comment alone.

## What shipped 08-06 — dev-made artifacts are DISABLED in Simple, not hidden

Owner decision, same day. Routines and widgets created in OPEN were filtered out
of `routine.list` / `widget.list` under SAFE, so switching Developer → Simple
emptied the library and the rail: the person's own work looked deleted, and the
one row that could have said otherwise was the row being withheld. They are
listed and visibly disabled now, carrying the plain sentence dispatch already
refuses them with. [SAFETY.md](SAFETY.md) owns the rule (renamed there: *artifact
hiding* → *artifact disabling*); `CLAUDE.md`, `architecture.md`, `data-model.md`,
`flows.md` and the engineering spec were amended in the same commit.

What is worth carrying forward from building it:

- **The marker is a REASON, not a boolean.** The wire carries
  `unavailable: {reason, message}` — absent on a usable row — with `reason` an
  open slug vocabulary (`developer_abilities` today). A boolean would have needed
  a second field the first time a different cause appeared, and the parser on the
  frontend passes unknown slugs through rather than treating a cause it has not
  heard of as "fine".
- **DISPLAY ONLY, said in the code that computes it** (`rpc/constants.py`,
  `_unavailable_marker`). The refusals in `routine.run` / `widget.run` and the
  engine's per-step `dev_only` check are the enforcement and were left exactly
  where they were; **if the flag and dispatch disagree, dispatch wins**. Both
  refusals now read their sentence from the same constant the list does, so the
  surface and the refusal cannot drift into telling two stories.
- **Listing a dev row meant validating its spec against OPEN**, which is a hole
  if written carelessly. It is scoped to rows *stamped* `'open'`; a command spec
  behind a `'safe'` stamp still fails SAFE validation and stays hidden. The
  existing test for that (`..._whatever_it_claims_it_was_made_in`) is what caught
  the careless version — mutating the branch to an unconditional OPEN turns it
  red.
- **Two existing tests asserted the OLD rule** and were rewritten to the new one,
  refusal halves untouched:
  `test_dev_artifacts_hidden_in_safe_and_returned_in_open_round_trip` (now
  `..._listed_disabled_...`) and `test_custom_created_widget_hidden_and_refused_in_safe`.
  Both now assert the reason on the wire *and* that running is still refused.
- **The rail's "first thing on screen claims the source" rule grew a new way to
  break, and it is the 07-26 duplicate-source bug run backwards.** A stat widget
  made in Developer is now IN the Simple rail, drawing a reason instead of
  numbers — and it was still *claiming* its source, so the ambient connection
  block stood down for a row that shows no connections and the person's
  Ollama/Anthropic rows disappeared. Found by re-reading that rule against the
  new row type, not by a failing test; `claimsItsSource` now lets an unavailable
  widget draw without claiming. Any future row that renders something OTHER than
  its source's value has to answer the same question.
- **A parser can be dead code and nothing notices.** `normalizeRailRoutines` in
  `useWidgets.ts` was the only producer of the rail's routine marker, and every
  rail test builds its own `RailRoutine` object — so deleting the new line left
  the whole suite green. It is exported and unit-tested now; the shape of that
  gap (a normalizer whose consumers are all tested against hand-built fixtures)
  is worth looking for elsewhere.

## Measured 07-31 — two keychain spikes for the vault redesign

Nothing shipped; two claims of
[docs/secrets-and-keychain-plan.md](secrets-and-keychain-plan.md) were
**measured** before the design could rest on them (the scrutiny pass flagged
both as load-bearing and unverified). A ~60-line spike binary
(`security-framework` 3.x, modern `SecItem*` API) was signed with the
`Addison Dev` identity and driven with 4–5 s timeout guards, so a dialog
classifies as "prompted" without anyone answering it.

**Spike 1 — does creator trust survive a rebuild under a self-signed cert? NO.**

```
add   (build A, signed)   -> OK  25ms   added
read  (build A, signed)   -> OK  29ms   read 28 bytes        # same binary: silent
# append a no-op fn, rebuild, re-sign with the SAME "Addison Dev" identity
read  (build B, signed)   -> TIMED OUT (5s)                  # dialog appeared
```

An item created via `SecItemAdd` by one build **prompts when read by the next
build signed with the same certificate**. Without a team ID there is no stable
partition for trust to attach to, so the Chrome/VS Code zero-prompt steady
state requires an Apple-issued identity (Phase 3). The dev floor is one
explained sequence per **rebuild** (relaunches of the same binary stay silent —
consistent with the owner's original "asked only after a rebuild" report). This
**falsified** the draft's claim that app-created items give zero-dialog steady
state under the dev cert — which is exactly what the spike was for. One open
variable only the owner can measure: whether a single *Always Allow* (a
partition-list edit) is durable across rebuilds — plan §13.6.

**Spike 2 — does the data-protection keychain work under the current signing? NO, with a number.**

```
dp-add  -> ERR  11ms  Error { code: -34018, "A required entitlement isn't present." }
```

`kSecUseDataProtectionKeychain` needs provisioned entitlements; the Phase-3
deferral is now measured rather than assumed. (Also re-verified in passing:
attributes-only queries against a real item raise no dialog, and cleanup via
the `security` CLI deleted the spike item without a prompt.)

---

## What shipped 07-31 (later) — step 5.5 items 4 and 5: step 5.5 is COMPLETE

**Item 4 — output redaction + the tool-call audit trail.**

- **`agent_core/redaction.py`** — stdlib `re` only. Every rule is anchored to a
  vendor prefix or a structural marker (`sk-ant-`, `ghp_`, `AKIA`, `xox[baprs]-`,
  `AIza`, `Bearer `, PEM blocks). An unanchored "long alphanumeric" rule would eat
  git SHAs, base64 and UUIDs out of ordinary output, and **a redactor that mangles
  innocent text is one people switch off**.
- **The seam is the ORCHESTRATOR, not each provider.** There are five
  `_translate_history` functions — five places for a sixth provider to miss.
  `conversation.messages` is handed to a provider at exactly two sites, so
  redaction happens there, provider-agnostically, and a provider added later is
  covered for free.
- **Redact toward the model, never into the store** (the plan's owner decision 2,
  resolved). The wire gets a throwaway view; `conversation.messages` and the
  SQLite rows keep the real bytes, because scrubbing the person's own record would
  destroy the evidence that a leak happened. Both halves are asserted.
- **`tool_audit`** — one row per tool DECISION on every branch, at all three
  dispatch sites. Five outcomes: `granted | denied | forbidden | confined_out |
  dev_only`. It closes a real hole: `read_web_page` is LOW so it writes no
  `action_snapshots` row — **the tool most exposed to prompt injection left no
  record of which hosts it fetched** — and a refusal left none at all. EXCLUDED
  from snapshots on the `tool_grants` precedent (a restore that rewrote the record
  of what happened would be worse than no record). **This satisfies step 7's log
  dependency.**

**Two findings worth keeping:**

1. **The audit attribution was wrong by one round, and a test caught it.** The
   granted row was written *before* `execute`, naming redactions from the previous
   outbound send — but a tool's output is scrubbed on the *next* send, so that row
   described a round carrying none of this tool's output. Moved after execution and
   attributed to the result itself. The bug was invisible in the code and obvious
   in the assertion, which is the argument for asserting on values rather than on
   "did it run".
2. **The docs-drift test fired twice, correctly, on work that was otherwise
   green.** Once for the new `tool_audit` table missing from `data-model.md`'s ER
   diagram, and once because §9's restored G3 sentence had drifted more than 500
   characters from its scope marker. Both are exactly the drift those tests were
   written for, and neither would have been caught by review.

**Item 5 — design-doc §9 brought current.** Bullet 1 ("capability allow-list, not
a shell") is amended in bullet 2's own idiom — name the property, say where the
boundary moved to — rather than left standing as an aspiration. New **§9.x, "What
this does NOT defend against"**, with ten named boundaries: an attacker who can
already write to `~/.addison`, a person who approves a malicious command, prompt
injection in OPEN, exfiltration through an approved command (and why
`network-outbound` is granted anyway), the data-not-code gap, `sandbox-exec`'s
deprecation, platforms with no profile, hardlinks, multi-user machines, and the
Python-side zeroization limit. OpenClaw and Claude Code both state their
boundaries plainly; Addison's docs previously read as though the floors were
absolute.

Four mutations proven on the new guards: send-boundary redaction removed, the PEM
rule disabled, the audit's redaction naming dropped, and the view returning
originals — each kills its own test and nothing else.

---

## What shipped 07-31 — step 5.5 items 1, 2 and 3: the OPEN harness gets a floor

**G3's overclaim is closed.** `run_command` no longer executes in the Agent Core;
it crosses the ShellBridge and the shell runs it under a seatbelt profile built
from the live workspace-trust roots. The headline test is live and
mutation-proven in `shell/src-tauri/src/exec.rs`. Items 4 (redaction + audit log)
and 5 (design-doc §9) remain, and **step 7 is still downstream of item 4**.

### Items 1 + 2 — execution moved, and confined

- **`shell.runCommand`** (`protocol.py` / `protocol.ts`, hand-synced) carrying
  `{command, timeoutMs, writeRoots}` → `{stdout, stderr, exitCode, sandboxed}`.
- **`shell/src-tauri/src/exec.rs`** — the seatbelt profile, generated per call.
  Order is the security argument: `(deny default)`, broad reads, `(deny
  file-write*)`, then per-root allows, then **the data-dir denies LAST** so the
  floor beats a trusted root that contains it. The shell re-derives the data dirs
  itself; the core's `writeRoots` is an input to the boundary, never the boundary.
- **`sandboxed` is answered honestly.** On macOS a missing `sandbox-exec` REFUSES
  the command rather than running it bare; elsewhere the command runs and the tool
  prints a note above the output. A silent unsandboxed fallback would have been
  this project's own anti-pattern — a guard reporting success while doing nothing.
- **`ExecutionContext.trusted_roots` is a CALLABLE, not a list.** A list captured
  when the turn began is a trust snapshot, and the one direction it can be stale
  in is the dangerous one: a root revoked mid-turn would stay writable for the
  rest of it.

**Nine findings worth keeping:**

1. **The timeout did not exist, and a green test said it did.** `run_with_timeout`
   signalled the direct child. But `/bin/sh -c "echo x; sleep 30"` FORKS — `sleep`
   is a grandchild, so the shell died and the real work ran on, still holding the
   write end of the stdout pipe, so `drain` blocked until it finished by itself. A
   600ms budget took the full 30 seconds; `run_command`'s advertised 30s ceiling
   was unenforced for every compound command; and the shell's IPC worker was held
   for as long as the longest orphan lived. **The test passed the whole time**,
   because it asserted on the OUTPUT — and the stderr note is appended on the
   timeout path whether or not the kill landed. It was found only by noticing that
   every `cargo test` run in the session reported ~30s. Fixed with
   `process_group(0)` + `kill(-pgid)`; the test now asserts ELAPSED TIME, which is
   the property, and is the one thing an output-shaped assertion cannot see.
   Suite went from 30s to 0.6s. **If a test's subject is a deadline, assert the
   clock.**
2. **A negative sandbox test can pass because the sandbox never ran.** Mutating
   the allowlist to `/` broke the profile outright — and *both* negative tests
   went green, because a rejected profile also means the forbidden file is absent.
   The strongest possible false green, on the one boundary the step exists to
   build, and only the positive test noticed. Every negative test now writes a
   marker into a permitted path in the same command and asserts the marker landed:
   marker present + target absent means the sandbox ran and refused; marker absent
   means the test proved nothing and says so.
3. **The headline test belongs in Rust.** Python can prove the core refuses to
   *ask*; only the shell side can prove that a command which *is* approved cannot
   escape. Keeping it in Python as an `xfail` would have left the definition of
   done in the process that no longer enforces anything.
4. **No `subprocess` in the core is a property of the FILE, not of a call** — so
   it is pinned by a source test. It checks call shapes (`subprocess.run`,
   `os.system`, …) rather than the words, because the module has to be able to
   explain what was removed and why; a guard that forbids the prose is paid for by
   deleting the explanation.
5. **A test made deterministic can stop testing the wiring.** The headline
   originally read the data dir from `ADDISON_DB_PATH`, which other Rust tests
   mutate — it passed alone and failed in the full run, the worst shape of flake on
   the one test the step is judged against. The fix made `seatbelt_profile` a pure
   function of (write roots, protected dirs)… which meant every test passed the
   floor in explicitly, and **dropping `addison_data_dirs()` at the call site
   killed the floor with all six tests still green**. `the_handler_feeds_the_real_protected_dirs_into_the_profile`
   closes it by asserting on the argv the handler actually builds. Purifying a
   function for testability moves the untested part to its caller; the caller needs
   its own test the same day.
6. **Two granted capabilities that nothing needed.** The profile's non-file
   grants were written defensively — copied from the shape such profiles usually
   have — and included unfiltered `mach-lookup` and `ipc-posix-shm`. Measured
   afterwards: git, node, python, pytest and npm all work without either, and
   unfiltered `mach-lookup` is a known way OUT of a seatbelt profile (ask a system
   daemon to act on your behalf). Both removed. `sysctl-read` stays because it is
   genuinely load-bearing — without it `node` aborts in `os.GetOSInformation`
   (a `uname` call) and every `npm` invocation dies with a native stack trace.
   Under `(deny default)` each grant is a decision; the set is now pinned by
   `the_profile_grants_no_capability_beyond_the_measured_set`. **Measure, don't
   copy.**
7. **The network was denied by accident, and that was the wrong default.** No
   `(allow network*)` under `(deny default)` broke `git fetch`, `npm install`,
   `pip install` and `curl` — with a DNS error that reads as a broken machine
   rather than a policy. It also bought nothing: **the command's output already
   travels to a cloud provider**, so a profile that blocks `curl` and then hands
   the same bytes to a model over HTTPS has closed only the useful half of the
   harness. `network-outbound` is now granted deliberately, with the reasoning in
   the profile; `network-bind` is not (a model-issued command has no business
   opening a listening socket, and the 30s ceiling makes a dev server pointless).
   Exfiltration remains item 4's problem, exactly as broad reads are.
8. **A hand-synced protocol asserted on one side is asserted on neither.** Nothing
   covered the actual frame: the Rust tests called the inner functions, the Python
   tests stopped at the bridge, and `test_protocol_drift` covers the method NAME
   only. A renamed field would have passed both suites and failed the first time
   the app ran. Worse, the first version of the Rust contract test *still* missed
   it — both inbound fields are read with `unwrap_or`, so a rename silently
   becomes a default. Each field is now asserted **through its effect**: the write
   lands (so `writeRoots` arrived) and the command dies in 600ms (so `timeoutMs`
   arrived). Renaming either key on either side now fails a test.
9. **The bridge needed a third timeout budget.** A command waits on the
   *command's* deadline plus slack, not the shell's default 60s — otherwise a
   legal 45-second build is reported as a wedged shell while it keeps running with
   nobody to receive its output. `test_only_the_keychain_calls_wait_at_a_persons_pace`
   now pins three budgets instead of two.

### Item 3 — the hardline denylist

**What shipped:**

- **`policy.command_denied_path(command, data_dir)`** — the predicate. Returns
  `(token, direction)` or None, where direction is INSIDE a denylisted root or
  CONTAINS one. `denylisted_roots(data_dir)` = the existing `_protected_dirs()`
  (data dir, its snapshot sidecar, `~/.addison`) **plus** `~/.ssh`, `~/.aws`,
  `~/.gnupg`; `.env` is matched on basename, wherever it lives.
- **`tools/base.call_is_forbidden(tool, args, data_dir)`** — the generic
  dispatcher, reading a new optional `command_text(args)` on the tool.
  `run_command` is the only implementer.
- **Checked at all three dispatch sites**, above the gate: `orchestrator.py`,
  `routines/engine.py`, `rpc/widgets.py` (the Run pill), each bound to the live
  data dir by the server. A boundary one of the three does not enforce is not a
  boundary — SAFE invariant 3's reasoning applied to containment.
- **Two refusal messages, not one** — INSIDE is a dead end, CONTAINS names the
  next move ("name the folder inside it that you actually mean").

**Five findings from the build itself:**

0. **"Which data directory?" needs exactly one owner.** The mistake below was
   then made a second time, in the opposite direction: the fix left
   `command_denied_path` re-deriving the data dir from the environment, so a store
   opened anywhere but the default would have been protected in name only. Nothing
   in the tree does that today, which is precisely why it would have shipped.
   `denylisted_roots` and `call_is_forbidden` now **require** a data dir — no
   convenience default — the live server binds them via
   `WorkspaceMixin._is_forbidden_call` (the same shape as `_is_trusted_path`), and
   a source test pins the three modules allowed to derive one at all. A signature,
   not a convention, because the convention lost twice.
1. **A second copy of the floor is worse than none.** The first cut also ran
   `workspace_trust_allows` on a path-bounded tool's resolved path — a "cheap
   second layer". It re-derived the data dir instead of using the live one the
   caller holds, so under the test harness (`conftest` points `ADDISON_DB_PATH` at
   `tmp_path`) **every ordinary file in a test was judged to be inside the data
   dir**: 11 step-5 tests failed. Confinement already applies the floor at these
   same three sites, with the right data dir. The branch was deleted, not fixed.
   The failure was loud only because the step-5 suite is thorough; the same
   mistake in a less-covered predicate would have shipped.
2. **`command_text` is deliberately not `permission_detail`.** The detail is
   capped at `MAX_PERMISSION_DETAIL_CHARS` (120) for the card and the Activity
   Panel. A denylist reading the capped string stops seeing the dangerous path of
   any command long enough to push it past the cap — a hole you get for free by
   reusing the obvious accessor. Pinned by
   `test_a_long_command_is_scanned_in_full`.
3. **`{` and `}` must not be token separators.** Splitting on them tears
   `${HOME}/.addison` into three pieces, none of which resolves to the data dir.
   Caught by the `${HOME}` case in the forbidden list, which was written before
   the code.

**The known cost, and it is deliberate:** the ancestor direction means `ls ~`,
`ls .` and `ls /` are refused outright, not carded. Read and write are not
distinguishable in a `shell=True` string — that is #48's lesson, three times over
— and `rm -rf ~` takes the recovery floor with it. Naming a subfolder works, and
the **two refusal messages** exist for this: a CONTAINS refusal says which move to
make next, so the model corrects in one turn instead of reporting the task as
blocked. One shared message made every `ls ~` read as a dead end.

**Now that item 2 has landed, this direction is scaffolding.** The kernel refuses
`rm -rf ~` while allowing `ls ~`, which is the distinction the string could never
make. `command_denied_path` says so in its own docstring: delete the CONTAINS
direction and `_names_a_directory` with it, rather than tuning either. Left in for
now only because nothing measures how often it fires — that is item 4's audit log.

**One test was passing for the wrong reason** and is worth naming: the
attached-short-flag vector `grep -f/Users/x/.ssh/id_rsa .` matched on its trailing
`.` (a CONTAINS hit against home), so the flag path it was written to cover went
entirely untested. It is now spelled against the real home with no trailing token.
A vector that passes for the wrong reason is worse than no vector — it occupies
the slot where the real check should be.

Every guard line was mutation-proven in a scratch copy outside the repo (seven
Python mutations: each of the three sites, both directions of the predicate, the
`.env` rule, and `command_text` reading the truncated detail; plus three Rust
mutations against `exec.rs`).

---

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

**A trace exists now (2026-07-31).** `ADDISON_KEYCHAIN_TRACE=1` prints every
keychain touch from BOTH processes to the one stderr the dev terminal shows:
`keychain.rs` prints the OS touches (the thing that costs a dialog) and
`shell_bridge.py` prints the core call site (which the shell cannot see). Off by
default in every build — a keychain trace on by default is a keychain trace in
someone's support-log paste — and it never carries key material: `KeyRead::Found`
renders as the bare word `found`, never the value, its length, or a prefix
(`a_trace_line_never_carries_key_material`).

**What the owner reported on 07-31, and why it narrows things:** two dialogs on
launch, **naming the same item**, where pressing *Allow* still raised the second
and *Always Allow* stopped it. That is diagnostic: **"Allow" is a single-ACCESS
grant** — it records nothing — while "Always Allow" writes an ACL onto the item.
So two dialogs for one item means **two separate OS reads**, which `KEY_CACHE` and
`OS_KEYCHAIN` were built to prevent. A read that returns `Found` is cached and
cannot prompt twice, so the first read is not returning a key; the two candidates
are a remembered `Unreadable` that a `fresh` per-turn probe deliberately retries
past, and `NothingSaved`, **which is never cached at all** on the reasoning that
"nothing saved costs no dialog". The trace distinguishes them in one launch.

**RESOLVED 2026-07-31 — the trace settled it, and step 1 shipped.** A live trace
of two launches showed exactly ONE `OS-TOUCH` on `provider-key:anthropic`, and the
read did not return until BOTH dialogs were answered (Esc on the second aborted it
to `unreadable`). So the two prompts were never two reads: they are two ACL
authorizations for a single `SecKeychainFindGenericPassword`, against an app
identity macOS cannot match. Everything on Addison's side was ruled out first —
`keyring` 3.6.3 makes one `find_generic_password` call per read, there are no
duplicate keychain items (an earlier check used `sort -u`, which would have hidden
them), and the login keychain is `no-timeout` so the first dialog was not an
unlock. Cause 1 below was the whole story.

**Two things the same trace established, worth keeping:**

- **A dismissed dialog costs the whole session.** Esc on launch left every later
  read answering `unreadable (no OS touch)` from `FAILED_READS` for 74 seconds —
  the app ran keyless, and the UI said "not connected" with no explanation.
  Recovery works exactly as designed: the first user MESSAGE probes with
  `fresh=true`, re-reads, and finds the key. Worth surfacing in the UI rather than
  leaving the person to guess.
- **`nothing-saved` is never cached, so it re-reads forever.** OpenAI and Google
  are read from the OS on every 60-second `stats.get` poll, permanently. Free in
  dialogs today (no item exists, so there is nothing to authorize) — but it is the
  one read path with no memory in front of it, and it is the shape the original
  three-prompt cascade had.

**Agreed plan, in order — do not skip to the end:**

1. **A stable self-signed development certificate. — SHIPPED 2026-07-31.**
   `sign-and-run.sh` + `.cargo/config.toml`: a cargo **runner** signs each dev
   build with the `Addison Dev` identity and then execs it. The runner is the only
   available seam — `npm run tauri dev` builds and launches in one step,
   `beforeDevCommand` runs before the Rust build, and `bundle.macOS.signingIdentity`
   applies to `tauri build` only. Identifier goes from `addison-<per-build-hash>`
   (adhoc) to a stable `addison`, so one "Always Allow" survives every later
   rebuild. Fails OPEN on a machine without the identity (warns, runs unsigned):
   it is a convenience wrapper, not a security control, and a fresh clone must
   still build. Scoped to the app binary so `cargo test` is untouched, and
   `ADDISON_SIGN_ONLY=1` signs without launching, because a script whose normal
   path ends in `exec` into a GUI window is otherwise untestable.
   Fixes the actual cause: a stable signing identity means the ACL survives
   rebuilds and Always Allow works. Free. *The $99 Apple Developer Program is for distribution — signing,
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

## The step-1 ledger retirement (2026-07-24)

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
