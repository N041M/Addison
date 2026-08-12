# Test hardening plan

**Status: PARTIALLY ADOPTED.** Originated from a whole-repo mutation measurement on
2026-07-20; last reconciled against the tree 2026-07-26.

The standard the snapshot subsystem was held to, generalised, plus the open work still
needed to reach it.

**Trimmed 2026-07-26.** The original 2026-07-20 measurement tables, the live-bug
write-up (H1) and the cost estimates were removed: the bug is fixed, the numbers were a
record of a moment rather than a claim about the current suite, and several modules they
scored have since been rewritten. All of it is in git. **Re-measure before quoting any
figure.**

**Closed:** H1, H2, H3, H5, H6, H8 (PRs #48/#49) and H7 (overtaken).
**Open:** H9 (all three `PermissionCard.tsx` consent-surface defects are still live),
H10, H11, H13, H14, and the remainder of H4 and H12.

---

## 2. The standard, as a checklist

"As thorough as the recent code" is not a feeling about effort. It is five properties, each
checkable by someone else.

**S1: Mutation-proven.** Every test that pins a fix must be demonstrated to **fail when its own
line is reverted**, in a scratch copy outside the repo. A test that passes both before and after
its own fix is false coverage: rewrite it or delete it. Record the mutation next to the test.

**S2: Adversarial probes that run.** Reviewers write and execute scripts against real SQLite
files, real HTTP transports, real directories, trying to break the thing. Reading the code and
reasoning about it does not count and has repeatedly missed what a probe found in under a minute.

**S3: Fault injection.** For any recovery or consent path, inject the failure modes explicitly:
handler raises, handler returns the wrong type, handler returns `None`, transport times out, file
is unreadable, database is corrupt. Every one must **degrade inert**: fail closed, never fail open.

**S4: Doc-versus-code.** Every factual claim in prose is verified by execution. Where they
diverge, one of them changes. Two divergences found in this pass: `RiskTier.HIGH`'s docstring says
"SAFE mode: not permitted at all" while a HIGH tool sits in the SAFE view (§4.4), and CLAUDE.md
says the card "carries the exact command text… so the user knows precisely what they are
approving" while the component clips it to one line (§4.9).

**S5: Pin the call site, not just the unit.** A validator with 86% mutation coverage and a
0%-covered caller is an untested validator. For every safety check, name the production call sites
and pin each one.

### The anti-patterns this project has actually shipped

Not hypotheticals. Each of these is in the repo right now, and each shipped through review.

- **The transcribed table.** `test_run_command.py`'s destructive list contains exactly the
  separators the implementation handles. It agrees with the code by construction and cannot detect
  that the code is incomplete. **This one was hiding a live file-deletion bypass** (§1).
  *Antidote:* test the decision procedure, not a list of examples. **Closed by #48**, and by
  deletion rather than by a better table; there is no classifier left to transcribe.
- **The hand-built literal.** `get_device_key_response_shape` built
  `let response = json!({...})` **in the test body** and then asserted
  `response.get("privateKey").is_none()`, asserting that a dict the test just wrote without a
  `privateKey` has no `privateKey`. Its own comment said "mirroring the handle() arm". It could not
  fail. This is why adding the ed25519 private seed to the real response (a direct G1 breach)
  passed the whole Rust suite. *Antidote:* call the production builder. **Closed by #49:**
  `device_key_response()` / `sign_relay_response()` are extracted in `keychain.rs`, the tests call
  them, and `no_keychain_response_ever_contains_the_private_seed` serialises every keychain
  response and asserts the seed appears in none.
- **The fixture that omits the call production makes.** Every gate stub in `test_orchestrator.py`
  and `test_routines.py` is `lambda tool_id: ...`, one argument. Production's per-invocation
  destructive path calls `on_request(tool_id, detail)` with two. Those fixtures are *structurally
  incapable* of exercising the path they appear to cover.
- **The fixture that omits the distinguishing case.** Both `test_prune_*` tests use rows that are
  *all* already past the cutoff, so flipping the cutoff's sign changes nothing they observe. Only
  the `keep_last` floor is pinned; the age half of the rule is invisible.
- **The self-referential bound.** `test_over_long_title_rejects` asserts on `MAX_TITLE_LEN + 1`
  and `MAX_TITLE_LEN`, so it adapts to any value: raising the bound 60 → 60000 passes. It can
  detect that a comparison exists, never that the bound is right. *(The contrast here used to
  be `test_pinned_cap_is_six`, which hard-pinned its number. That test was **deleted**:
  `assert MAX_PINNED == 6` asserts that a constant equals its own literal and stayed green
  with both enforcement sites removed. The pinned-cap section of `tests/test_widgets.py` now
  reaches the number behaviourally instead, by pinning `MAX_PINNED` widgets and watching the
  next be turned away. That is the better shape for a bound too: exercise it, do not restate
  it, which qualifies §5's "pin the number or do not write the test".)*
- **The passing serialization test.** The `web_search` forgery (a page written with apostrophes
  could close a dict and forge a user message inside the untrusted wrapper) was live from the day
  the tool shipped, through every review, with tests green. It was found only when a new tool made
  the same mistake visible.

**One test that would have caught the thing is worth more than thirty that describe it.**

---

## 3. The floors first

For each floor: the test that fails if it silently stops holding. Where none exists, that is the
top of the plan.

### G2, "Addison never triggers itself": **CLOSED 2026-07-24 (#49).**

> `tests/test_g2_no_self_trigger.py` now walks every core module, not just
> `snapshot_manager.py`. The rule is stated as *"nothing that fires work on a SCHEDULE or
> after a DELAY"* rather than a ban on concurrency, so the legitimate worker thread, `Event`
> waits and blocking `queue.get()` stay green and the test does not get deleted by the next
> person it annoys. Its anti-vacuity check pins the **subpackages** covered rather than a
> module count; a count lets you drop `providers/` entirely and stay above the floor. The
> assessment below is the state it was written against, kept for the reasoning.

Exactly **one** test existed repo-wide: `test_snapshot_subsystem_never_schedules_itself`
(`test_snapshots.py:2129`), an AST scan for `threading`/`sched`/`asyncio`/`signal` imports **scoped
to `snapshot_manager.py` alone**. Verified: no other file in `tests/` references G2 or
self-triggering.

For `main.py`, `orchestrator.py`, `routines/engine.py`, `rpc/*.py`, or the Rust `agent_process.rs`,
the honest answer to "what fails if G2 stops holding" is **nothing**. And the one test's technique
does not generalise: `main.py` imports `threading` legitimately, so a bare import ban would either
fail on correct code or have to be weakened to uselessness.

**Write:** a repo-wide structural test that walks the AST of all of `agent_core/` and asserts that
no module reachable from a tool, routine, widget, or provider can call the orchestrator's turn
entry point, and that no timer/scheduler callback is ever wired to it. Then mutation-test it by
adding a `threading.Timer` that fires a turn, and confirm it dies.
**Kills:** `g2-01`, a `threading.Timer(60, orchestrator.run_turn)` registered in `rpc/routines.py`.
**Cost:** ~1 day, including getting the reachability analysis honest enough not to be noise.
**Urgency:** this must land **before step 8** (the automation keyword gate), which is the step that
makes G2 load-bearing rather than theoretical.

### G1, "keys never reach the frontend or SQLite": **the two named holes are CLOSED (#49); (b) and (c) below are not.**

> **Closed:** the `keychain.rs` builder split and the private-seed sweep (K1/K10), and
> `shell_bridge`'s three survivors (error frames, timeouts and `get_provider_key`) are covered
> by `tests/test_shell_bridge.py`, whose G1 retention test checks the instance, the **class**
> and the **module** namespace. **Not written:** the systemic sentinel-key sweep (b) and the
> error-path fuzzing (c). Those two are still the right next work on this floor.

Strong on the provider side (58 key-related test references) and strong in Rust *around* the
keychain seam (20 tests). But:

- **`shell_bridge.get_provider_key`, the function that actually moves keys, has zero tests**, and
  two independent mutations to it survived: dropping the `{"provider": provider}` argument (wrong
  key, or a silent fall back to the legacy `provider-key:primary` entry), and returning the whole
  frame repr instead of `result["key"]`.
- **Mutation K1 (adding the ed25519 private seed to the real `keychain.getDeviceKey` response)
  survives all 31 Rust tests**, because the two tests that would catch it are hand-built literals
  (§2). This is a G1 breach that ships green today.
- Key-cache promises are untested: "evicted on Remove" (K3) and refreshed on Replace (K4) both
  survive. Remove a key, keep chatting, and requests still succeed with the removed key.

**Write:** (a) the `keychain.rs` builder split: extract `device_key_response()` /
`sign_relay_response()` out of `handle()` following the existing `app_build.rs` pattern, rewrite
the two tests to call them, and add one test that serialises **every** keychain response and
asserts the private seed's base64 appears in none. (b) A **systemic** Python test: run an
end-to-end turn with a sentinel key, then scan every SQLite table, every emitted IPC frame, the
transcript, `usage_log`, and every snapshot sidecar for the sentinel. One test, all sinks, so a
new sink is covered by construction. (c) Error-path fuzzing: provider raising with the key in the
message, in `__cause__`, in an `httpx.Request` repr, in a retry log.
**Kills:** `K1` (private seed in response), `K3`/`K4` (cache coherence), `sb-03` (frame repr
returned as key), `sb-04` (provider argument dropped).
**Cost:** ~1.5 days. The Rust half is ~40 lines and **is already written and proven** in the
scratchpad; it fails with K1 applied and passes without.

### G3, "guaranteed rollback": **comfortably the strongest, and the model for the rest.**

146 tests, mutation-hardened across three rounds, fault-injected at eight failure modes,
doc-verified. `test_restore_always_works_from_a_broken_config` passes. The source-level guard
`test_no_snapshot_query_filters_on_created_in_mode` is exactly the right instinct: it stops someone
adding `AND created_in_mode = ?` next quarter, which a behavioural test never would.

**Write:** nothing new. **Re-verify by mutation** on a schedule (§7): revert
`_permanent_row_matching`, revert the retention "newest two" exemption to one, and confirm named
tests die. Coverage is 86% line on `snapshot_manager.py`, which is precisely why coverage is not
the signal here (§7).

### G4, "undeletable anchor on weakening": **OVERTAKEN by step 2. The caller exists and is tested.**

Permanence is enforced by `RAISE(ABORT)` **database triggers** rather than a `WHERE` clause
someone can forget, which is structurally right. When this was written **`mint_anchor()` had no
production caller**, because the Custom-profile guard toggle that mints an anchor was step 2.

Step 2 landed 2026-07-24 (PR #52). `guards.set` (`agent_core/rpc/guards.py`) is the caller:
validate → compute weakenings → **mint the anchor FIRST**, refusing the whole set if the
anchor cannot mint → persist, with fingerprint dedupe so weaken→tighten→weaken churn cannot
grow an unbounded permanent list. `tests/test_custom_profile_guards.py` covers it, and the
coordinator personally reproduced the mutation kill for `guards.set` ignoring a mint failure.

**The recommendation here (a `strict=True` xfail asserting the minting site exists) is
therefore moot; do not write it.** The pattern it borrowed was
`test_the_addison_data_dir_can_never_be_workspace_trusted` in `tests/test_ipc_snapshots.py`,
which has itself been flipped live by step 5. Both are examples of the technique working:
write the rule before the capability, and it fails loudly the moment the capability lands.

---

## 4. The work plan, ordered by consequence

**Closed items (H1, H2, H3, H5, H6, H8; and H7, overtaken) were removed on
2026-07-26; they shipped in #48/#49 and the detail is in git.** What remains
below is open work only.

Ordered by what a user experiences when it fails, not by module layout. Every item names the
mutation it must kill; an item that cannot name its mutation is not specified yet.

### Tier 0: a silent failure here means a user cannot get back to a working machine

**H4. Registry undo enforcement: substance, not presence. PARTIALLY DONE (#49).**
*Ranks here because the spec calls this "the single most important test in the codebase".*
**Closed:** a non-callable `undo` (`undo = "a string"`) is now refused at registration;
`tools/registry.py` checks `not callable(own_undo)` alongside `is None`. **Still open:** the
round-trip undo test per tool, and the `RiskTier.HIGH` docstring divergence. A *callable* no-op
cannot be caught statically; #49's comment says so rather than implying otherwise, and the
round-trip test is the honest answer: **it is still absent for every tool.**
When this was written the check was `getattr(type(tool), "undo", None) is not None and not
abstract`, so both of these registered at HIGH and landed in the SAFE view:

```
  no-op undo             REGISTERED at RiskTier.HIGH  <- check did not fire
  non-callable undo      REGISTERED at RiskTier.HIGH  <- check did not fire   (undo = "not even callable")
  SAFE view contains: ['sneaky']
```

CLAUDE.md's "do NOT satisfy this with a no-op `undo()`" was prose with no enforcement.
- ~~`test_undo_must_be_callable`~~: **done (#49).**
- `test_a_no_op_undo_does_not_satisfy_the_undo_requirement`: **not written, and deliberately
  so.** A callable no-op cannot be caught statically; both candidate enforcements (a
  `__code__.co_code` body check, or a declared `undo_payload` contract) were judged worse than
  saying so plainly and relying on the round-trip test below.
- `test_no_high_tier_tool_is_ever_in_the_safe_view`: **open.** Resolves the `RiskTier.HIGH`
  docstring divergence (S4) one way or the other.
- **Round-trip undo per tool: open, and the highest-value item left in H4.** For every
  registered non-LOW tool, execute → assert observable state changed → `undo()` → assert state
  byte-identical to before. This is the only test that proves an `undo` is real, and it is
  **absent for every tool**.
**Killed:** `reg-04` (non-callable undo accepted). **Open:** `reg-03` (no-op undo accepted),
`reg-02` (Protocol-default `undo` satisfies the check).

### Tier 1: a safety invariant switches off silently, or the consent surface lies

**H9. `PermissionCard`: three verified defects on the consent surface. STILL ENTIRELY OPEN
(re-verified against the tree 2026-07-26; the `truncate` class, the `indexOf` split and the
`RUN_PREFIX` re-parse are all still in `PermissionCard.tsx`).** ~1 day.
- The command renders in a `truncate` class: single-line, ellipsis. The
  core truncates at `MAX_PERMISSION_DETAIL_CHARS = 120` *precisely so the whole thing can be
  shown*; the card then clips to whatever fits the rail.
  `git status --short && rm -rf ~/Documents/…` reads as `git status --short && rm -r…`. The full
  text is in a `title=` tooltip, but **hover is not consent**, and the personas are 54 and 68. This
  is an S4 divergence from CLAUDE.md's "carries the exact command text".
- A multi-line command hides its tail (same cause).
- `splitCommand` uses `description.indexOf("run: ")` (first occurrence anywhere), so a SAFE
  description reading *"This routine will run: it needs your calendar to do that."* renders
  ordinary prose in the mono chip whose visual grammar means *this is the exact command*.
- **Structural:** `RUN_PREFIX = "run: "` re-parses a sentence the core composes in `main.py`
  (`f"This time it wants to run: {detail}"`, and a second copy on the CLI path). Two hardcoded
  strings, two languages, nothing connecting them. Reword the core and the mono chip silently
  disappears with zero test failures, the same string-punning-across-a-trust-boundary shape as
  the `web_search` forgery.
  **Fix: send `command` as a structured field on `permission.requestGrant`**, and add that payload
  to the drift-fixture rig (H12).
**Kills:** `pc-01` (`truncate` retained), `pc-02` (prose rendered as command), `pc-03` (core
reword silently disables the chip).

**H10. Gate fault injection and denial semantics.** ~0.5 day. `authorize` returns whatever
`_on_request` returns, so a handler returning `None` currently yields a non-DENIED status and the
call proceeds. Inject: handler raises, returns a non-`PermissionStatus`, returns `None`, blocks
past a timeout. All four must **fail closed**. Plus: a turn-denial is bypassable by reclassifying
the same call as non-destructive; `revoke()` has no effect on the OPEN auto-grant path; and
`revoke_all()` (called after a G3 restore) leaves denials intact.
Also decide the SAFE-path `detail` question (§0.4): SAFE deliberately uses coarse session grants,
but *computing* a `detail` and then discarding it at `request()` is not the same decision as not
computing one. **Owner call needed.**
**Kills:** `gate-08` (`None` from handler proceeds), `gate-05` (`grant()` no longer clears denial),
`gate-09` (reclassification bypasses a turn denial).

**H11. `FileState` allowlists + the Core→Shell seam. STILL OPEN; the seam is unchanged.**
~1.5 days. `filesystem.rs` grew from 4 tests to 21 during step 5 (the workspace file tools and
the dangling-symlink fix), but **every handler still takes `&AppHandle`**, so none of those tests
cross the seam this item is about, and the source-level protocol-string test was never written.
Four Rust mutations survive because the tests will not cross the `AppHandle` seam: `delete_file`
deleting **any** path,
`restore_file` writing **any** path, `read_scoped_file` accepting a raw path as a handle (the core
escapes the picker), and `open_external` opening `file://` / `javascript:`. "Addison can only
remove a file it just created" is currently guaranteed **by a comment**. Make `FileState`
constructible without an `AppHandle` (take `&FileState`, not `&AppHandle`). Add a source-level test
that the `shell.*`/`keychain.*` string literals matched in Rust equal the `Method` constants in
`protocol.py`: 13 strings hand-synced across a language boundary with nothing enforcing them.
**Kills:** `F1`, `F2`, `F3`, `F4`, `F7` (renamed Rust arm).

**H12. Extend the payload-drift fixture rig. PARTIALLY DONE.** ~0.5 day left.
`tests/ipc_fixtures.py` + `test_ipc_fixture_drift.py` + `parsers.fixtures.test.ts` is **the single
best idea in the repo's test strategy**: Python generates from live handlers, vitest consumes, so
a shape change breaks both CIs. The step-4/5 rigor pass added five: `workspace.list`,
`costPlan.propose`, `endpoint.proposeFromConversation`, `tool.activityUpdate` and `profile.get`
(which carries `mode`). It did so because the absence had a measured cost: the frontend read
`workspace.list` as `{roots}` while the core sent `{folders}`, the trusted-folder list rendered
permanently empty in the shipped app, and both suites stayed green because each asserted its own
idea of the shape.
**Still missing, and it is the one that matters most:** `permission.requestGrant`, the surface
where the entire destructive-prompt rule is rendered to a human. Add it, and
`snapshot.restoreLastWorking`'s result. **Standing rule from that pass: add a fixture for every
new payload a parser consumes.**
**Kills:** `M13` (`riskTier` → `tier` on the routine ask-user card).

### Tier 2: data loss or a wrong answer, no invariant breached

**H13.** ~2 days total, in this order:
- `undo_manager.prune` cutoff sign (`um-04`): correct cutoff retains four recent snapshots,
  flipped retains two, silently discarding a day of undo history. Both existing tests use rows
  already past the cutoff (§2).
- `rpc/undo.py` reporting `ok: True` when `result.success` is False: the user is told the action
  was reversed when it was not, in the one surface whose entire job is "you can get back".
  **37% coverage, no direct tests.**
- `rpc/routines.py` `_ask_user_continue` returning `True` instead of the waiter's answer, so a
  routine whose step failed **continues without consent**. Adjacent to SAFE invariant 3.
- `web_search` / `read_web_page`: property-test that for **arbitrary page bytes** the serialized
  tool result parses back to a dict with `untrusted_note` intact and no additional top-level
  message boundary. Assert at AST level that `append_tool_result` uses `json.dumps`; the
  docstring itself calls this "load-bearing", and load-bearing prose deserves a test.
- `providers/router.py`: `resolve()` returning `None` instead of raising turns a clear "no model
  configured" message into an `AttributeError` mid-turn; disconnecting the selected primary leaves
  a dangling selection.
- `tool_call_parser.py` (81 lines, no dedicated test file): a misparsed tool call is a wrong
  action taken silently. Fuzz it.
- HTTP error page parsed as search results (`ws-03`): plausible-looking garbage instead of "I
  couldn't reach the web."

**H14. `open_link` IP vetting: a decision, then a test either way. STILL OPEN, and now
cheaper.** ~0.5 day. Re-verified 2026-07-26: `open_link` (SAFE, **model picks the URL**) still
validates the scheme only, in Python and again in Rust, with **no IP vetting**, while
`read_web_page` does full resolved-IP SSRF and DNS-rebinding vetting against the same threat. So
injected page text can steer Addison to open `http://192.168.1.1/admin?reset=1` in the user's
real browser with their cookies. Browser-mediated, so weaker than direct SSRF, but it is the
same attack, and `open_link` simply predates the vetting built for `read_web_page`. **This is an
accident of build order, not a decision.** Step 4 factored that vetting into
`agent_core/net_vetting.py` with the vetting *decision* as a parameter, so if the answer is
"vet it", the mechanism is now a call rather than a rewrite.

### Tier 3: loud, bounded, cheap

Example tests are correct here; do not spend mutation effort. `calculator.py`, `skills.py`,
`profiles.py`, `models_catalog` labels. A wrong number is visible and costs nothing.

---

## 5. What NOT to test

Effort spent on tests that cannot fail is **worse than nothing**, because it buys false confidence.
This project has shipped that mistake three times (§2). Each of these is better verified by looking,
by types, or not at all.

**Do not write, and delete on sight:**
- **"Renders without crashing" tests.** They cannot fail in any way anyone cares about.
- **DOM snapshot tests of `SettingsPage`.** It has been rewritten twice since this was written
  (step 2's guard panel, then the dark redesign's surface idiom); every snapshot becomes noise,
  and noise trains people to regenerate snapshots without reading them.
- **Tests that rebuild the production value in the test body** and assert on their own literal.
  Two were live in `keychain.rs` and were the highest-value deletion in the repo; #49 replaced
  them with assertions over the extracted builders (§2).
- **Tests whose bound is derived from the constant they are testing** (`MAX_TITLE_LEN + 1`). Pin
  the number or do not write the test.

**Do not automate, verify by looking:**
- **"Does it look right in dark mode."** Contrast, rhythm, whether the violet accent stays
  reserved for actions and live state instead of reading as decoration, whether the
  sans/mono pairing holds. A jsdom test asserting class strings here
  **passes while the screen is wrong**, strictly worse than an honest manual checklist. Keep it in
  TESTING-CHECKLIST §13.
- **`MermaidDiagram` SVG output, animation timings, font loading.**
- **Supervisor restart timing and stderr inheritance** (`agent_process.rs` P2/P4). Cheaper and more
  reliable to verify by running the app and killing the core. An automated test is slow, flaky, and
  proves little. *(Note: this is the one place where "don't test it" and "it has 0 tests" coincide:
  the routing decision inside `handle_line` **should** be extracted and tested, §H11; the process
  lifecycle should not.)*

**Do not build:**
- **A WebDriver/Playwright harness over the packaged Tauri app.** Slow, flaky on CI, and the
  failures it finds (a dialog doesn't open, a font doesn't load) are exactly the failures that are
  cheaper to find by opening the app.
- **Unit tests for `rfd`, `arboard`, `open::that`, or the `updater.rs` stub.** Testing that a
  library is a library.

**Better served by types than tests:** the six TS-only payload interfaces (`ChatMessage`,
`PermissionRequest`, `ActivityUpdate`, `RiskTier`, `PermissionStatus`, `ModelRole`) have **no Python
counterpart to diverge from**: they are built ad hoc in `main.py`/`rpc/*.py`. Do not write drift
tests for all of them; give the load-bearing ones a generated fixture (H12) and leave the rest to
review.

---

## 7. How to keep it

**Is mutation testing worth wiring into CI? Not as a blocking gate. Yes as a sampled nightly.**

Blocking CI on mutation testing is the wrong trade here. The suite runs in 8.2s; a full mutation
pass over `agent_core/` is minutes to tens of minutes, and worse, **mutation scores are noisy at the
margin**: equivalent mutants (three in this pass alone, §0) mean a hard threshold either gets
tuned down to uselessness or gets someone in the habit of overriding a red gate. A safety gate
people learn to override is worse than no gate.

**Proposed, in order of cost:**

1. **Nightly sampled mutation run**: 25 mutations drawn from a committed catalogue, weighted toward
   the modules this document scored worst, reporting a trend rather than a pass/fail. Cost: a few
   hours to wire, near-zero to run. This is the honest signal.
2. **The mutation catalogue itself, committed** (`tests/mutations/catalogue.toml`), one entry per
   named mutation in §4, each naming the test that must die. This is the cheap proxy, and it is
   the highest-leverage item in this section: it converts "we mutation-tested it once" into a
   durable artifact that a reviewer can re-run against a PR touching that module.
3. **A blocking gate on the floors only.** ~8 mutations against G1/G2/G3/G4 enforcement points.
   Seconds to run, and these are the assertions where an equivalent mutant is least likely because
   the code is small and deliberate.
4. **Review checklist item, one line:** *"For each test added, name the mutation it kills."* Free.
   It is also the single question that would have caught the `run_command` table, the keychain
   literals, and the prune fixture.

**Do not** add `@vitest/coverage-v8` and set a threshold. **Coverage is the weak signal here and
this repo has the receipt:** `snapshot_manager.py` sits at **86% line / 202 branches** and that is
the subsystem that shipped with its headline requirement inverted through eight green gates. Read
coverage as *"which lines has no test ever touched"* (a floor on ignorance), never as *"which
behaviour is pinned"*. It is worth measuring occasionally to find the 0% modules (it found
`rpc/undo.py` at 37% and `shell_bridge.py` at 63%); it is not worth gating on.

**What stops the decay.** The suite grew 385 → 658 in one day. Growth like that is exactly when
transcribed tables and hand-built literals get written, because the fastest way to add a test is to
describe what the code does. Three defences, cheapest first: the one-line review question above;
the committed catalogue; and one standing rule: **a test added alongside a bug fix must be
demonstrated to fail against the pre-fix code, and the PR must say so.** That last one costs a
reviewer thirty seconds and is the only mechanism here that directly targets the failure mode that
produced round one.

**One documentation debt, half cleared.** `VERIFICATION.md` used to carry a test-count claim that
went stale twice; it now carries **no counts at all**, which is the same policy HANDOFF.md adopted
for the same reason. What is still true is that the live-driver scripts it describes **do not
persist**: they are rewritten every session. Promote the live driver to a committed, marked test
(§8) if that is worth two days; until then the runbook says plainly that the scripts are ephemeral,
which is better than reading as current and not being.

---

## Appendix: probe scripts

**Mostly gone. Do not plan on them.** They live in a session-scoped scratchpad
(`/private/tmp/claude-501/-Users-karel-Desktop-Addison/9ae359b3-…/scratchpad/hardening/`). Checked
2026-07-26: the directory tree survives on this machine, but the Python probes have been swept.
**`verify/`, `widget/`, `classifier/`, `gate/`, `modeleak/`, `registry/` and `probes/` are all
empty**, including both headline reproductions. What is still on disk is `rust/` (the mutation
harness and `fixed/`), `frontend/` (the proposed `PermissionCard` tests, in a full scratch Vite
project), and the `mut/` + `mutation/` working copies. None of it is under version control and
none of it survives a `/tmp` sweep.

The inventory below is therefore a record of what was run for the measurement, not a pointer to
runnable code. Recreating any one probe is an afternoon; the durable artifact this plan asks for
is the mutation catalogue (§7.2), which is **still not committed**, and that gap is precisely why
this appendix has decayed into a list of filenames.

| Path | What it demonstrated |
|---|---|
| `verify/classifier_e2e.py` | **The live bug.** Real gate → real execute, deletes a throwaway dir with 0 cards |
| `verify/undo_substance.py` | No-op and non-callable `undo` both register at HIGH |
| `classifier/probe.py`, `classifier/e2e.py` | Original classifier bypass discovery |
| `modeleak/probe.py` | `is_dev_only` dead code; a `dev_only` tool reachable under SAFE |
| `gate/probe.py` | Turn-denial bypass, `revoke` no-op on auto-grant, SAFE `detail` loss |
| `registry/noop.py` | Undo-check presence-vs-substance |
| `widget/probe.py` | Widget validation fail-closed (clean, no findings) |
| `rust/mutate.py`, `rust/fixed/` | 23 Rust mutations; **the proven keychain fix** |
| `frontend/src/__tests__/proposed.permissionCard.test.tsx` | 5 tests, 3 failing against the real component |
| `mutation/`, `mut/`, `probes/` | The Python mutation harnesses and results |

*The lesson, since it cost something: a probe that proves a floor is broken should be promoted to
a test in the same change that fixes the floor, not left in a scratchpad. #48 and #49 did that:
`tests/test_run_command.py` and `tests/test_dev_only_boundary.py` are what survived of the two
probes above.*
