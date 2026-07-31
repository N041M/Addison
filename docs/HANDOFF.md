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

## Next up

**Phase-2 step 5.5 is DONE (2026-07-31/08-01) — all five items.** Next is **6, 7
or 8**, and they are independent of each other. `ROADMAP.md` has the descriptions,
`CLAUDE.md`'s Build order has the dependencies. What is worth repeating here:

- **G3 is no longer overclaimed.** `run_command` executes in the Rust shell under
  a Seatbelt profile built from the live trusted roots, with the data-dir denies
  emitted after every allow. The headline test —
  `an_approved_command_cannot_delete_the_recovery_floor` in
  `shell/src-tauri/src/exec.rs` — is live and mutation-proven, and SAFETY.md's
  qualification came off.
- **Step 7 (MCP) is unblocked on one of its two counts.** The audit log exists
  (`tool_audit`, written on all five outcomes at all three dispatch sites), so
  "gated, logged, undo-aware" is satisfiable. **It is still blocked on the
  MCP-in-SAFE question** in [`KNOWN-GAPS.md`](KNOWN-GAPS.md) — a server declares
  its own risk, and admitting a tool to SAFE on that say-so breaks SAFE invariant
  2 through a path the registration check cannot see. Close that first.
- **Two things only the owner can decide**, both queued in KNOWN-GAPS: whether a
  401 should mark a provider needs-attention (it currently changes nothing, so a
  revoked key fails every turn forever), and whether `/Applications/Addison.app`
  joins the seatbelt denies (the floor protects Addison's data, not its code —
  `exec.rs` is now where that deny would go, so it is cheap).

**If you are picking up the keychain thread:** read
[`secrets-and-keychain-plan.md`](secrets-and-keychain-plan.md) before touching
`keychain.rs`. It is a repair-first plan (presence out of the keychain into
`provider_config`; self-heal foreign items by delete-and-recreate; `Intent`
replaces nine probe symbols), with the encrypted-vault rewrite kept as a
destination behind named triggers. **§10.1 lists eight traps a vault build must
re-fix** — the vault draft itself is gone (see the warning below), and that list
is what survived of it.

**Two queued, contract-first, not started:** rework local-model setup (state-aware —
not-downloaded → one-click download plus a source link; downloaded → how to connect
it; and more open-source models), and skills file-upload (an uploaded text file's
contents become the skill's guidance text — editable, previewed, size-limited).

## Uncommitted work on `master` (2026-08-01)

**Everything below is written, tested and green — and NOT committed.** If you are
a new session and `git status` is dirty, this is why. Roughly 30 files: step 5.5
in full, the keychain diagnosis and its signing fix, the secrets plan, and three
small gap closures.

**A warning paid for in this session:** an untracked file was renamed and
rewritten, and ~700 lines of design work vanished with no git history to recover
from — the doc that replaced it even claimed its history held the original. Commit
early here; the tree is carrying a lot that exists nowhere else.

Suggested split if you commit it in pieces: (1) step 5.5 — `exec.rs`, the denylist,
redaction, `tool_audit`, their tests and docs; (2) the keychain diagnosis — the
trace and `sign-and-run.sh`; (3) the secrets plan (pure docs); (4) the three gap
closures.

## Branch and PR state (verified 2026-08-01)

**No open pull requests. `master` carries everything** — work from it, and see the
uncommitted-work section above before assuming a clean tree.

- **PR #58 merged** (`a22badd`, 2026-07-26): the dark v4 redesign, the
  adversarial-review fix wave, the docs passes, the brand/icon fixes and thread
  windowing. `redesign/dark-v2` is still pushed, is fully contained in `master`,
  and is safe to delete.
- **Since then, direct on `master`:** the brand icon-pipeline fix (`cc70ea8`), the
  pointer-glow revert (`e98828c`), the widget rail / model-menu fixes (`3ab1159`),
  the starfield removal (`07cc9ee`), per-token streaming (`d2174c1`, `0d6eec6`) and
  the documentation restructure (`f4ad86a` onward).
- **`windowed-thread` no longer exists**, locally or on the remote — its commit was
  rewritten as `839bcff` and merged with #58. Thread windowing **is** shipped.
- `archive/thread-window-wip` and `archive/icon-gen-wip` are parked worktree
  experiments, kept only so the attempts are recoverable. Neither is for merge.

**Re-read this section immediately after any merge.** It was false for ninety
minutes on 2026-07-26 because `7444c8e` described the branch state accurately while
the redesign was unmerged, and PR #58 then falsified six passages without touching
the file that contained them. No gate catches that.

## Three traps this session hit, all the same shape

Worth thirty seconds before you write a test here, because each one cost real time
and each looked green:

1. **A deadline test that asserts output proves nothing about the deadline.**
   `run_command`'s 30-second ceiling did not exist for any compound command (the
   shell forks; killing the child left the grandchild holding the pipe) and the
   test passed throughout, because it checked the message rather than the clock.
   Fixed with a process group; the test now asserts elapsed time.
2. **A negative test passes when the mechanism never ran.** Two sandbox tests
   asserted "the forbidden file is absent" — equally true when the profile was
   rejected and nothing executed. Every negative test now writes a marker into a
   permitted location in the same command and asserts the marker landed.
3. **Purifying a function for testability moves the untested part to its caller.**
   Making `seatbelt_profile` take its protected dirs as an argument fixed a flake
   and simultaneously let a mutation delete the floor at the call site with all
   six tests green.

The habit that catches all three: **mutate the line you think matters and confirm
the test dies.** It has been wrong four times in this repo now.

## Where the project stands

- The v1 build order (engineering-spec §11, steps 1–11) is implemented and merged.
  Phase-2 steps 1–5 are built and merged; **5.5 is complete but uncommitted**
  (above). **6, 7 and 8 remain.**
- Addison is a **butler**: Developer = a Claude-Code-class coding harness; Simple =
  an all-in-one companion; Custom tunes prompting guards. Safety means **guaranteed
  rollback**, and that now has code and tests behind it in BOTH modes — the
  OPEN-mode caveat that stood from 2026-07-26 was closed by step 5.5.
- **The dark v4 UI is on `master`.** The Fern direction is gone from the tree, not
  merely superseded; `docs/design-brief-fern/` is kept as history only.
- **Gates all green on `master`:** pytest, pyright 0 errors (the remaining
  diagnostics are pre-existing `reportMissingImports` for `pytest`/`httpx` — pyright
  has no venv), ruff, vitest, ESLint, `tsc --noEmit`, `vite build`, `cargo test`.
  `tsc` now covers the test files too (`npm run typecheck` runs both configs — the
  second one found nine real errors the day it was added).
  **Counts are deliberately not written down** — they went stale twice in one day,
  and a stale number reads as a claim. Commands are in `CONVENTIONS.md`;
  `VERIFICATION.md` is the runbook.
- **CI runs the same three job groups** (`.github/workflows/ci.yml`) on every PR and
  every push to `master`. Keep it green.
