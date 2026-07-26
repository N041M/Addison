# Addison — Verification Runbook

The *coordinator's* list: automated gates, scripted end-to-end proofs, and the
review obligations that a green CI run does not cover. Companion to
`TESTING-CHECKLIST.md`, which is the manual desktop pass and owns every
"open the app and look at it" step — this file does not duplicate them.

Run this before any wave is committed and PR'd. **Green gates are not the bar**
— see HANDOFF.md, "Read this first: the standard this repo is held to".

## 1. Automated gates (all must be green, from repo root)

```bash
# Python: full suite. Agents may only ever raise the count.
agent_core/.venv/bin/python -m pytest tests/ -q

# Python lint + types
agent_core/.venv/bin/ruff check agent_core tests
npx --yes pyright                      # config: pyrightconfig.json

# Frontend: lint, strict tsc, vitest, vite build
cd shell && npm run lint && npx tsc --noEmit && npm test && npm run build

# Rust shell
cd shell/src-tauri && cargo test && cargo clippy --all-targets
```

No test-count thresholds are written here on purpose. They went stale twice in a
day, and a stale number reads as a claim — run the suite and read the number off
the run. `.github/workflows/ci.yml` runs the same three job groups on every PR
and on every push to `master`.

From a **git worktree**, prefix pytest with `PYTHONPATH=$PWD` and use the main
checkout's interpreter — the venv lives in `/Users/karel/Desktop/Addison`.

## 2. Live-driver end-to-end proofs (real API, pennies on haiku)

Pattern documented in `HANDOFF.md` ("The live-driver pattern"); the scripts are
written per session and do not persist. The mechanics: spawn
`agent_core/.venv/bin/python -m agent_core.main` with `ADDISON_DB_PATH` at a tmp
dir, play the Rust shell from a reader thread, answer permission cards, cap
turns, `modelId: claude-haiku-4-5`. **Never point a driver at `~/.addison`** —
`live_db_guard` blocks it, and the guard exists because a probe script once wrote
an undeletable row into the owner's real database.

Scenarios that must pass (each was proven once; rerun after any core change):

1. **Plain turn** — send one message with an explicit modelId → streamed
   reply, `result.ok true`, result carries `userMessageId`/`assistantMessageId`.
2. **Tool refusal doesn't poison** — shell answers `shell.saveNewFile` with an
   error ("file already there") → turn still completes with a plain
   explanation; the NEXT turn succeeds (no API 400 from unpaired tool_use).
3. **Rewind (edit-and-resend)** — rewind to a returned `userMessageId` → ok;
   anchor and everything after are gone from memory AND store; a follow-up
   turn shows no knowledge of anything at/after the anchor.
4. **Undo/redo cycle** — save (file exists) → `undo.undoLastAction` (file
   gone, `canRedo: true`) → `undo.redoLastAction` (file back, byte-identical,
   `canRedo: false`) → undo again works.
5. **Denied step still delivers** — allow `web_search`, deny `open_link` →
   reply contains the found information in chat, no retry-nagging.
6. **Persistent-DB relaunch** — run any turn, kill the core, start a second
   core on the SAME `ADDISON_DB_PATH` → chatting still works (idempotent
   conversation row).

Phase-2 added two more that have been driven live once each and are worth
rerunning when their surfaces change: the **Custom-profile flow** (dispatch,
G4 anchor mint, anchor dedupe, the D7 re-weaken notice, C6 under SAFE, and
`snapshot_now` writing through `main()`'s late-bound holder), and **routing**
(a real turn carrying `answeredWith`, the vanished-custom-chain-id note, and
`test_local_only_never_reaches_the_relay`'s live counterpart).

## 3. Safety-critical diff review (coordinator, before commit)

Any wave that touches these files gets a read-through against the invariants in
CLAUDE.md, not just a green run:

- [ ] `agent_core/tools/registry.py` — undo-required-at-registration intact
      (`undo` present, own, **and callable**); `open_only` /
      `allow_missing_undo` still two separate dimensions.
- [ ] `agent_core/permissions/gate.py` — grant/deny semantics unchanged
      (denials one turn only; grants persist; no new bypass); destructive calls
      never enter the coarse SAFE flow under any `auto_grant_scope`.
- [ ] `agent_core/policy.py` — `mode_for_profile` still derives OPEN from
      Developer **and** Custom; guards effective only under Custom.
- [ ] `agent_core/snapshots/snapshot_manager.py` — still imports stdlib plus the
      two schema-mirroring leaves and nothing else; retention and payload
      version still module constants; no query filters on `created_in_mode`.
- [ ] `agent_core/snapshots/scope.py` — every new table and every new column of a
      captured table is explicitly captured or explicitly excluded.
- [ ] `agent_core/snapshots/undo_manager.py` — redo stays opt-in per tool;
      new actions clear the redo stack.
- [ ] `agent_core/net_vetting.py` — resolve → vet → connect to the vetted IP →
      follow no redirects → re-vet every hop; `credential_headers` stay a
      separate parameter and never cross an origin.
- [ ] `agent_core/providers/anthropic_provider.py` + `models_catalog.py` — no
      key material in errors/logs; key strip/validate intact.
- [ ] `shell/src-tauri/src/filesystem.rs` — `created`/`deleted` allowlist
      gates structurally unchanged; create_new (never overwrite) preserved; the
      data-dir refusal still canonicalizes the whole path, not just the parent.
- [ ] `shell/src-tauri/src/keychain.rs` — no key ever logged or in an error; no
      keychain response carries the ed25519 private seed.
- [ ] `agent_core/protocol.py` ↔ `shell/src/types/protocol.ts` — method
      strings byte-identical (drift test also enforces), and **every new payload
      a frontend parser consumes has a fixture** in `tests/ipc_fixtures.py`.
- [ ] Module boundary: `tools/`, `providers/`, `routines/` still don't import
      each other (`tests/test_module_boundaries.py` also enforces).
- [ ] No user-facing string reworded without reason; no jargon introduced.

## 4. Open items (verify or decide)

Everything here was checked against the tree on 2026-07-26.

- [ ] **RoutineLibrary shares one `values` map across routines**
      (`shell/src/components/RoutineLibrary.tsx`). The engine is safe against
      *unknown* names — `routines/engine.py` builds defaults from
      `routine.variables` and `resolve_template` only reads names the template
      mentions — so a stray key is inert. The live edge is a **name collision**:
      fill routine A's `path`, then run routine B without input, and B's default
      `path` is overridden by A's value. Scope `values` per routine id, or clear
      it when `filling` changes.
- [ ] **Empty-text `sendMessage` has no guard.** `_run_send_message`
      (`agent_core/rpc/conversation.py`) reads `params.get("text", "")` and never
      checks it; the CLI does. An empty message would persist a blank user turn
      that the rollback doesn't remove. Unreachable through the composer today —
      decide whether to add the guard.
- [ ] **Local-setup pre-flight HTTP runs on the read loop.**
      `_handle_start_local_setup` (`agent_core/main.py`) is an inline dispatch
      handler and calls `is_running()`, which can block frame delivery up to 5s.
      `availableRoles` was moved off the read loop for exactly this reason. Same
      shape as `shell.pickDirectory` blocking the worker on a modal dialog.
- [ ] **Stale docstrings / dead-looking-but-seam items** flagged by an earlier
      cleanup sweep and carried forward **unverified**: the `PermissionRequest`
      dataclass (`permissions/gate.py`), `ModelRouter.register`
      (`providers/router.py`), a claim in `openai_provider.py`, and
      `default_cloud_model([])`'s defensive gap (`models_catalog.py`). All four
      symbols still exist; whether the original observations still hold has not
      been re-checked. Human calls, none urgent — re-verify or delete the line.

### Closed since this file was last written

- **Routine engine crash-on-raise** — a tool that *raised* crashed the run,
  bypassed the `on_failure` policy and stranded the `routine_runs` row at
  `running`. Fixed; a raise is now a failed step, same as the live orchestrator.
  (Note the residual in HANDOFF.md: the dev-only guard in `routines/engine.py`
  re-implements `on_failure` inline instead of falling through to the canonical
  block.)
- **Double keychain probe per message** — one probe per turn.
- **Stream-chunk turn correlation** — `useTurn` holds `currentTurnRef`; Stop and
  every new turn reassign it, and a result arriving from an abandoned turn is
  dropped rather than overwriting "(Stopped.)" or a later answer.

## 5. Manual desktop pass

Owned entirely by **`TESTING-CHECKLIST.md`** — run it there rather than keeping a
second, drifting copy here. Note its branch warning: the visual sections describe
the dark v4 UI, which is on `redesign/dark-v2` and not on `master`.

## 6. Known-open polish items

- **Local conversation search.** The sidebar lists real conversations grouped
  Today / Earlier and renames on double-click, but there is no search field.
- **Scoped consent ("always allow" per site).** A SAFE grant is keyed by tool id,
  so after the first card every later `read_web_page` is ungated and
  model-addressed. Visibility is the mitigation that shipped (the Activity Panel
  names the host); narrowing the grant to a site is a permission-gate change.
- **"Not now" phrased by the model as a malfunction** ("didn't go through") in
  some replies.
- **Routine-save affordance discoverability** — a small link in the activity
  strip.
- **`primary.txt` widget guidance is interim-correct only.** It tells the model
  Addison cannot build custom-app widgets. True of today's code; wrong as a
  statement of the amendment's intent. Rewrite capability-aware in Phase-2 step 6.

Raw-markdown rendering, the conversation list, the token/cost meter and the
lingering empty-stack Undo button were all on this list and have all shipped
(`Markdown.tsx`, `Sidebar.tsx`, the rail's "Tokens this month", and the header's
`hasUndoableActions` guard).
