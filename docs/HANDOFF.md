# Addison — Session Handoff (2026-07-20)

For the next working session. Read **CLAUDE.md** first (repo law), then
**`docs/addison-scope-amendment-2026-07.md`** (the adopted scope shift — it
governs where it and the older specs disagree), then this.

**Next up: Phase 2 (CODE) of the scope amendment — start with the
snapshot/rollback subsystem (floor G3).** Details at the bottom.

## Where the project stands

- The whole v1 build order (engineering-spec §11, steps 1–11) is implemented and
  **merged to `master`**. No open PRs, no stacked chain — the stacked-PR era is
  over; every change now goes PR → `master` directly.
- **A major scope amendment was adopted 2026-07-20 — docs only, NO code yet.**
  `docs/addison-scope-amendment-2026-07.md` is authoritative. Addison is now a
  **butler**: Developer = a Claude-Code-class **coding harness**; Simple = an
  all-in-one **companion**; a new **Custom** profile tunes prompting guards.
  Safety is redefined as **guaranteed rollback**.
- **Gates, all green on `master`:** **385 pytest**, **pyright 0 errors**
  (repo-wide), ruff clean, **48 vitest**, ESLint clean, `tsc --noEmit` + `vite
  build` clean, **30 Rust tests**.
- **CI now exists** — `.github/workflows/ci.yml`, three jobs (python:
  ruff·pyright·pytest / frontend: eslint·tsc·vitest·build / rust: cargo test) on
  every PR and push to `master`. Keep it green.

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
    *verified-working* state, restore path itself unbreakable.
  - **G4** — turning a guard OFF in Custom mode mints an **undeletable anchor**
    that also captures the **app binary**.
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

1. **Snapshot/restore subsystem (G3)** — the floor everything else leans on.
   Build and harden **first**; its single most important test is *"restore always
   works, even from a broken config."* Covers auto + on-command snapshots and the
   app-binary capture used by Custom anchors.
2. **Custom profile + guard model + undeletable anchor** (`policy.py`).
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
Close the amendment's **§13 open questions** as you go (keyword syntax, snapshot
retention, Custom reachability, "verified-working" definition, auto-routing
depth, MCP-in-SAFE constraint, widget capability declaration, binary capture).

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

## Working conventions (established with the user)

- **Opus agents build, coordinator verifies.** Spawn Opus agents with EXACT,
  disjoint file-ownership lists; do shared-contract groundwork first (the
  hand-synced `agent_core/protocol.py` ↔ `shell/src/types/protocol.ts` — a drift
  test enforces sync, and a second fixture test now pins payload *shapes*); then
  personally verify the final tree (full suite, lints, pyright, builds, diff
  review of safety-critical code) before committing. Agent work survives a
  session death on disk — inventory `git status` and finish inline.
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

## Dev note: macOS keychain prompts

The shell caches provider keys in process memory for the session (one OS keychain
read per provider per launch — `keychain.rs` KEY_CACHE). If macOS still prompts
once per launch in development, click **Always Allow**; a dev rebuild changes the
binary signature, so expect one fresh prompt per rebuild. Packaged, signed builds
prompt at most once ever.
