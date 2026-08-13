# Addison: Verification Runbook

The *coordinator's* list: automated gates, scripted end-to-end proofs, and the
review obligations that a green CI run does not cover. Companion to
`TESTING-CHECKLIST.md`, which is the manual desktop pass and owns every
"open the app and look at it" step; this file does not duplicate them.

Run this before any wave is committed and PR'd. **Green gates are not the bar.**
See [`CONVENTIONS.md`](CONVENTIONS.md), "the standard this repo is held to".

## 1. Automated gates (all must be green, from repo root)

```bash
./scripts/gates.sh              # or: python | frontend | rust
```

**`scripts/gates.sh` is the gate list**, and it is a program rather than a
paragraph on purpose. This section used to hold its own copy of the commands, and
that copy is exactly how the disagreement happened: it named `npx tsc --noEmit`
(which checks `src` only), so the `tsconfig.test.json` gate that
[`KNOWN-GAPS.md`](KNOWN-GAPS.md) recorded as CLOSED had never actually run in CI;
it named `npm run lint` without `--max-warnings=0`; and it omitted `-D warnings`
from clippy. `.github/workflows/ci.yml` calls the same script, so there is no
second copy left to drift, and
`tests/test_docs_drift.py::test_ci_runs_the_gate_script_for_every_job_it_defines`
keeps it that way. Agents may only ever raise the test count.

No test-count thresholds are written here on purpose. They went stale twice in a
day, and a stale number reads as a claim. Run the suite and read the number off
the run.

From a **git worktree**, prefix pytest with `PYTHONPATH=$PWD` and use the main
checkout's interpreter (the venv lives in `/Users/karel/Desktop/Addison`).

## 2. Live-driver end-to-end proofs (real API, pennies on haiku)

The scripts are written per session and do not persist. **This is the canonical
description**; `HANDOFF.md` carried a second copy until 2026-07-26 and now points
here.

Spawn `agent_core/.venv/bin/python -m agent_core.main` from the repo root with
`ADDISON_DB_PATH` pointed at a tmp dir. A reader thread consumes stdout lines, and
frames whose method starts with `shell.` / `keychain.` are answered **by the
driver**, playing the Rust shell:

- `keychain.getProviderKey` → `{"key": ""}`, so the core falls back to its env key
- `shell.saveNewFile` → write in tmp, return `{path}`
- `shell.deleteFile` → delete **within tmp only**

`permission.requestGrant` notifications are answered with
`permission.respond {toolId, allow: true}`; everything else is request/response by
JSON-RPC id. Cap turns, use `claude-haiku-4-5` via `ADDISON_MODEL`, per-request
timeouts ~90s. This validated the whole stack for pennies, so reuse it.

**Never point a driver at `~/.addison`.** `live_db_guard` blocks it, and the guard
exists because a probe script once wrote an undeletable row into the owner's real
database.

Scenarios that must pass (each was proven once; rerun after any core change):

1. **Plain turn**: send one message with an explicit modelId → streamed
   reply, `result.ok true`, result carries `userMessageId`/`assistantMessageId`.
2. **Tool refusal doesn't poison**: shell answers `shell.saveNewFile` with an
   error ("file already there") → turn still completes with a plain
   explanation; the NEXT turn succeeds (no API 400 from unpaired tool_use).
3. **Rewind (edit-and-resend)**: rewind to a returned `userMessageId` → ok;
   anchor and everything after are gone from memory AND store; a follow-up
   turn shows no knowledge of anything at/after the anchor.
4. **Undo/redo cycle**: save (file exists) → `undo.undoLastAction` (file
   gone, `canRedo: true`) → `undo.redoLastAction` (file back, byte-identical,
   `canRedo: false`) → undo again works.
5. **Denied step still delivers**: allow `web_search`, deny `open_link` →
   reply contains the found information in chat, no retry-nagging.
6. **Persistent-DB relaunch**: run any turn, kill the core, start a second
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

- [ ] `agent_core/tools/registry.py`: undo-required-at-registration intact
      (`undo` present, own, **and callable**); `open_only` /
      `allow_missing_undo` still two separate dimensions; `removable` still the
      only route out of the registry, and **every mutator and every reader still
      on the one worker thread**. This file does not lock, and an `mcp.*` method
      answered inline on the read loop would be a mutation during somebody's turn.
- [ ] **Any dispatch path that resolves a tool id**: `find`, never `get`. An
      `mcp:` id leaves the registry on a refresh, a removal, a failed check, a
      snapshot restore and every restart, so a raise there costs a turn its
      `tool_result` or leaves a routine run recorded as `running` for ever.
- [ ] `agent_core/redaction.py`: every rule still anchored on a vendor prefix or
      a structural marker, and every rule still bounded on its WORST input: this
      runs on the worker thread over text a stranger chose, after that call's
      deadline has passed. A rule that can backtrack must say in its own comment
      why it cannot. Nothing may cut a string before the redactor has read it.
- [ ] `agent_core/memory/store.py`: a migration that rebuilds a durable table
      (`tool_audit`) still runs inside ONE explicit transaction and still builds
      the replacement under a separate name. `executescript` defeats an implicit
      transaction, and these rows are excluded from snapshots and never pruned, so
      an interrupted rebuild is the only way they can be lost.
- [ ] `agent_core/permissions/gate.py`: grant/deny semantics unchanged
      (denials one turn only; grants persist; no new bypass); destructive calls
      never enter the coarse SAFE flow under any `auto_grant_scope`.
- [ ] `agent_core/policy.py`: `mode_for_profile` still derives OPEN from
      Developer **and** Custom; guards effective only under Custom.
- [ ] `agent_core/snapshots/snapshot_manager.py`: still imports stdlib plus the
      two schema-mirroring leaves and nothing else; retention and payload
      version still module constants; no query filters on `created_in_mode`.
- [ ] `agent_core/snapshots/scope.py`: every new table and every new column of a
      captured table is explicitly captured or explicitly excluded.
- [ ] `agent_core/snapshots/undo_manager.py`: redo stays opt-in per tool;
      new actions clear the redo stack.
- [ ] `agent_core/net_vetting.py`: resolve → vet → connect to the vetted IP →
      follow no redirects → re-vet every hop; `credential_headers` stay a
      separate parameter and never cross an origin.
- [ ] `agent_core/providers/anthropic_provider.py` + `models_catalog.py`: no
      key material in errors/logs; key strip/validate intact.
- [ ] `shell/src-tauri/src/filesystem.rs`: `created`/`deleted` allowlist
      gates structurally unchanged; create_new (never overwrite) preserved; the
      data-dir refusal still canonicalizes the whole path, not just the parent.
- [ ] `shell/src-tauri/src/keychain.rs`: no key ever logged or in an error; no
      keychain response carries the ed25519 private seed.
- [ ] `agent_core/protocol.py` ↔ `shell/src/types/protocol.ts`: method
      strings byte-identical (drift test also enforces), and **every new payload
      a frontend parser consumes has a fixture** in `tests/ipc_fixtures.py`.
- [ ] Module boundary: `tools/`, `providers/`, `routines/` still don't import
      each other (`tests/test_module_boundaries.py` also enforces).
- [ ] No user-facing string reworded without reason; no jargon introduced.

## 4. Open items and known gaps

Owned entirely by **[`KNOWN-GAPS.md`](KNOWN-GAPS.md)**, the single live-issue
register. This section used to keep its own list; it drifted into a second
register holding four items the other one did not have, which is the same
two-copies problem §5 already solved by delegating. Moved there 2026-07-26.

## 5. Manual desktop pass

Owned entirely by **`TESTING-CHECKLIST.md`**; run it there rather than keeping a
second, drifting copy here. Its visual sections describe the dark v4 UI, which is
on `master` as of PR #58 (2026-07-26); the branch warning that used to live here
and there is retired.

## 6. Known-open polish items

Also moved to [`KNOWN-GAPS.md`](KNOWN-GAPS.md) (2026-07-26), for the same reason.
