# Addison — Verification Runbook

Every check that must pass before the current work (testing-pass fixes,
commit `bb94d06`, plus the three-agent cleanup sweep on top) is committed and
PR'd. Companion to `TESTING-CHECKLIST.md` (the manual desktop pass); this file
is the *coordinator's* list — automated gates, scripted end-to-end proofs, and
review obligations.

## 1. Automated gates (all four must be green, from repo root)

```bash
# Python: full suite (223+ tests; agents may only ever raise the count)
agent_core/.venv/bin/python -m pytest tests/ -q

# Python lint
agent_core/.venv/bin/ruff check agent_core tests

# Frontend: strict tsc + vite, zero errors
cd shell && npm run build

# Rust shell: 24+ tests, plus clippy if available
cd shell/src-tauri && cargo test && cargo clippy --all-targets
```

## 2. Live-driver end-to-end proofs (real API, pennies on haiku)

Pattern documented in `HANDOFF.md` ("live-driver pattern"); ready-made scripts
from this session exist in the session scratchpad but do not persist — the
mechanics are: spawn `agent_core/.venv/bin/python -m agent_core.main` with
`ADDISON_DB_PATH` at a tmp dir, play the shell from a reader thread, answer
permission cards, cap turns, `modelId: claude-haiku-4-5`.

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

## 3. Post-agent-sweep review (coordinator, before commit)

Diff review of every safety-critical file touched by the cleanup agents,
against the invariants (CLAUDE.md §safety):

- [ ] `agent_core/tools/registry.py` — undo-required-at-registration intact.
- [ ] `agent_core/permissions/gate.py` — grant/deny semantics unchanged
      (denials one turn only; grants persist; no new bypass).
- [ ] `agent_core/snapshots/undo_manager.py` — redo stays opt-in per tool;
      new actions clear the redo stack.
- [ ] `agent_core/providers/anthropic_provider.py` + `models_catalog.py` — no
      key material in errors/logs; key strip/validate intact.
- [ ] `shell/src-tauri/src/filesystem.rs` — `created`/`deleted` allowlist
      gates structurally unchanged; create_new (never overwrite) preserved.
- [ ] `shell/src-tauri/src/keychain.rs` — no key ever logged or in an error.
- [ ] `agent_core/protocol.py` ↔ `shell/src/types/protocol.ts` — method
      strings byte-identical (drift test also enforces).
- [ ] Module boundary: `tools/`, `providers/`, `routines/` still don't import
      each other (`grep` the imports).
- [ ] No user-facing string reworded without reason; no jargon introduced.

## 4. Open items flagged by the cleanup agents (verify or decide)

- [ ] **RoutineLibrary shared `values`** (frontend agent): one routine's
      entered variable values may leak into another's run. Verify the core's
      routine engine ignores unknown variable names and applies defaults;
      otherwise scope values per routine.
- [ ] **Stream-chunk turn correlation** (frontend agent): after Stop → new
      send, an abandoned turn's chunks could append to the new pending
      message. Needs a core cancel method or messageId correlation —
      polish-phase design decision, record it in the roadmap.
- [ ] **Routine engine crash-on-raise** (Python agent — FIXED, verify): a tool
      that *raised* (e.g. save refusal via the bridge) crashed the whole
      routine run, bypassed the on_failure policy, and stranded the
      `routine_runs` row at status 'running'. Now a failed step, same as the
      live orchestrator; 3 regression tests. Re-run the manual routine loop —
      this likely explains the "routines are iffy" report.
- [ ] **Double keychain probe per message** (Python agent — FIXED): one probe
      per turn instead of two blocking Core→Shell round-trips.
- [ ] **Empty-text sendMessage** (Python agent — OPEN): the JSON-RPC path has
      no empty-text guard (the CLI does); an empty message would persist a
      blank user turn that the rollback doesn't remove. Unreachable through
      the composer today — decide whether to add the guard.
- [ ] **Local-setup pre-flight HTTP on the read loop** (Python agent — OPEN):
      `is_running()` can block frame delivery up to 5s; availableRoles was
      moved off the read loop for exactly this reason. Design tension for the
      polish phase.
- [ ] Stale docstrings/dead-looking-but-seam items the agents flagged
      (PermissionRequest dataclass, router.register, openai_provider claim,
      default_cloud_model([]) defensive gap) — human calls, none urgent.

## 5. Manual desktop retests still owed (user, in the app)

After relaunching `npm run tauri dev` (Rust changed → recompiles):

- [ ] **Rewind** — "Rewind to here" on a user message: it leaves the thread,
      its text lands in the composer, nothing runs until Send; after Send the
      model shows no memory of the rewound-away turns.
- [ ] **Undo → "Do it again"** — save a file, Undo (gone in Finder), redo
      button appears; "Do it again" restores the identical file; the redo
      button disappears after any new tool action.
- [ ] **Engine kill** (`pkill -f agent_core.main`) — "stopped — restarting…"
      then "Addison's engine restarted — you can keep chatting."; chat AND
      the model picker work normally afterwards (catalog re-synced). A second
      kill stays down with the restart-the-app notice.
- [ ] **Routines, full loop, twice** — propose → save → Run now → "Done —
      every step finished." → run again (second run: expect the save step to
      refuse politely if the file still exists — by design) → Remove.
- [ ] **Stop button** — send, hit Stop, send a new message immediately: the
      stopped answer must NOT reappear, and the new turn must not be
      interrupted (the agent-fixed race).

## 6. Known-open polish items (not blockers, tracked for the polish phase)

- Raw markdown (`**bold**`) rendered literally in the thread.
- "Not now" on the permission card phrased by the model as a malfunction
  ("didn't go through") in some replies.
- Routine-save affordance discoverability (small link in the activity strip).
- Undo button lingers when the undo stack is empty (plain no-op message).
- Stream-chunk turn correlation (see §4).
- Conversation list & local search; scoped consent ("always allow");
  cost visibility — the adopted roadmap items.
