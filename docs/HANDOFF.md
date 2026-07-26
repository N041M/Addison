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

**Phase-2 step 5.5 — containment for the OPEN harness**
([plan](step-5.5-containment-plan.md)), then 6, 7 and 8. `ROADMAP.md` has the
descriptions and `CLAUDE.md`'s Build order has the dependencies; the two things
worth repeating here:

- **G3 is currently overclaimed in OPEN mode.** `run_command` can delete the
  recovery floor's own files, so the guarantee holds in SAFE and does not hold in
  OPEN. Ship 5.5's pre-gate denylist first — it is hours of work and closes the
  obvious case immediately ([SAFETY.md](SAFETY.md) has the detail).
- **Step 7 (MCP) is blocked twice over:** on 5.5's audit log, and on the
  MCP-in-SAFE question in [`KNOWN-GAPS.md`](KNOWN-GAPS.md). Do not start it before
  both are closed.

**Two queued, contract-first, not started:** rework local-model setup (state-aware —
not-downloaded → one-click download plus a source link; downloaded → how to connect
it; and more open-source models), and skills file-upload (an uploaded text file's
contents become the skill's guidance text — editable, previewed, size-limited).

## Branch and PR state (verified 2026-07-27)

**No open pull requests. `master` carries everything** — work from it.

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

## Where the project stands

- The v1 build order (engineering-spec §11, steps 1–11) is implemented and merged.
  Phase-2 steps 1–5 are built and merged. **5.5, 6, 7 and 8 remain.**
- Addison is a **butler**: Developer = a Claude-Code-class coding harness; Simple =
  an all-in-one companion; Custom tunes prompting guards. Safety means **guaranteed
  rollback**, and that has code and tests behind it — with the OPEN-mode caveat
  above.
- **The dark v4 UI is on `master`.** The Fern direction is gone from the tree, not
  merely superseded; `docs/design-brief-fern/` is kept as history only.
- **Gates all green on `master`:** pytest, pyright 0 errors (the remaining
  diagnostics are pre-existing `reportMissingImports` for `pytest`/`httpx` — pyright
  has no venv), ruff, vitest, ESLint, `tsc --noEmit`, `vite build`, `cargo test`.
  **Counts are deliberately not written down** — they went stale twice in one day,
  and a stale number reads as a claim. Commands are in `CONVENTIONS.md`;
  `VERIFICATION.md` is the runbook.
- **CI runs the same three job groups** (`.github/workflows/ci.yml`) on every PR and
  every push to `master`. Keep it green.
