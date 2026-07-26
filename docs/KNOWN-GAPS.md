# Known gaps and open design questions

**This file owns the live-issue register.** Everything Addison knows to be
incomplete, undecided, or deliberately deferred is here — nothing else in the tree
keeps a second list. `VERIFICATION.md` and `HANDOFF.md` both used to, and both
drifted from this one.

Nothing here is a bug report against shipped behaviour: these are tracked gaps,
deliberate deferrals and decisions waiting on the owner. A green test run does not
close any of them.

*(Extracted from `HANDOFF.md` on 2026-07-27, unchanged.)*

---

## Known gaps (deliberate or tracked, not bugs)

**This is the single live-issue register.** Items that used to sit in the step-1
ledgers, the deferred-with-reason table and the 07-24 "where a bug could have
entered" list were folded in here when those sections were retired (2026-07-26);
everything still open is below, and the closed rows went to git.

**The inline widget rail works but is not designed yet (UI/UX, owner call).**
The *functional* bug is fixed (`d35f113`, 2026-07-27): below 1024px the rail starts
closed and the header's «/» shows and hides it. What is still wrong is how it looks
and where it goes.

- **No transition.** The beside rail animates four properties on collapse —
  `width .35s, opacity .25s, margin-left .35s, transform .35s` — and the brief asks
  for exactly that (`design-brief-dark/README.md`: "Sidebar collapses via header «
  chevron (width/opacity/translate animate .35s)"). The inline form is
  conditionally *mounted*, so it pops in and out with no transition at all. Two
  affordances driven by one button behave visibly differently.
- **It lands in the wrong place.** It renders in `ChatThread`'s `footer`, so it sits
  in the reading column between the last message and the composer — which is why it
  read as "covering the chat window" even when short. It pushes the conversation up
  and competes with the composer for the eye.
- **The placement has no design authority behind it.** `IMPLEMENTATION.md` says
  "**Mobile** (<md): keep the existing drawer + inline-widgets structure" — that
  covers below 768px only. The 768–1024 band was invented by the 07-26 responsive
  work by extrapolating the mobile rule; the brief never specifies it. Worth
  deciding rather than inheriting.
- **Prior art that was deleted:** `BottomSheet.tsx` was removed in the v4 cleanup as
  orphaned "since widgets moved inline on mobile". A sheet or slide-over anchored to
  the header button — sibling to the existing `MobileDrawer` — is the obvious
  candidate, and it would fix the transition and the position together. Check git
  history before rebuilding it from scratch.

**Still open from the retired step-1 ledgers:**

- **`tool_grants` capture is still undecided.** Excluded today, and correctly so —
  the table is inert (nothing reads or writes it; `PermissionGate` keeps grants in
  memory). If grants ever persist, restoring a snapshot taken *before* the user
  revoked one would **reinstate** it: a privilege grant delivered by a deliberately
  ungated one-action button. If it is ever captured it must be an **INTERSECT**,
  never a replace.
- **`LiveDatabaseBlocked` should probably be a `BaseException`.** It subclasses
  `AssertionError`, so a broad `except Exception` swallows it and
  `JsonRpcServer._rebuild_into` reports "rebuild failed" instead of naming the
  guard. The block still HOLDS — nothing is written; what is lost is the loud
  message, in the one place a loud message is the whole point. Changing it alters
  every existing handler's behaviour, so it needs its own verification pass.
- **`routines/engine.py` — the dev-only guard duplicates `on_failure` handling.**
  It shapes its refusal as a failed step and re-implements abort / ask_user / skip
  **inline** instead of falling through to the canonical `if not result.success:`
  block (~L255). It matches that block today and will silently diverge the moment
  someone adds a fourth `on_failure` policy. Fix by restructuring so both paths
  share one block; it was written this way to keep a diff small, which was the
  wrong trade for a branch nobody exercises often.

**Open design questions, each blocking a specific step** (moved here from the scope
amendment's §13 when that document was retired, 2026-07-27 — the other four §13
questions were resolved during steps 1–3 and went with it):

- **Keyword-gate syntax (blocks step 8).** The exact prefix (`!run`, `arm:`,
  `sudo:` …) and the precise set of actions it gates. Owner's reading: running or
  arming powerful / OS-automation actions in the harness, never ordinary chat.
- **MCP tools in SAFE (blocks step 7).** Read-only only, a curated allowlist, or
  dev-only? And how MCP tool metadata declares undo-ability. **A server declares
  its own risk, so this cannot be taken on trust** — see the sharpened note in the
  spec's MCP section and item 4 of the step-5.5 plan.
- **Widget capability tiers and vocabulary (blocks step 6).** The exact safe
  interactive kinds (to-do/checklist, note, timer …), how a widget spec *declares*
  the capabilities it needs, how the tier check maps capabilities → mode, and how
  code-backed widgets are listed alongside declarative ones.
- **Auto-routing depth — v2 or now? (half-resolved.)** The AVAILABILITY half
  shipped in step 3: escalate/degrade on unavailable, rate-limit or network
  failure, with per-provider cooldown, a per-**attempt** deadline and the plain
  "X was busy, so Addison used Y" note. The CONFIDENCE half — quality-based
  escalation — remains v2 substrate, untouched.

**Moved here from `VERIFICATION.md` §4/§6 (2026-07-26)** — that file had become a
second live-issue register holding items this one did not have. All checked
against the tree on 2026-07-26:

- **`RoutineLibrary` shares one `values` map across routines**
  (`shell/src/components/RoutineLibrary.tsx`). The engine is safe against
  *unknown* names — `routines/engine.py` builds defaults from `routine.variables`
  and `resolve_template` only reads names the template mentions — so a stray key
  is inert. The live edge is a **name collision**: fill routine A's `path`, then
  run routine B without input, and B's default `path` is overridden by A's value.
  Scope `values` per routine id, or clear it when `filling` changes.
- **Empty-text `sendMessage` has no guard.** `_run_send_message`
  (`agent_core/rpc/conversation.py`) reads `params.get("text", "")` and never
  checks it; the CLI does. An empty message persists a blank user turn the
  rollback doesn't remove. Unreachable through the composer today — decide.
- **Local-setup pre-flight HTTP runs on the read loop.**
  `_handle_start_local_setup` (`agent_core/main.py`) is an inline dispatch handler
  and calls `is_running()`, which can block frame delivery up to 5s.
  `availableRoles` was moved off the read loop for exactly this reason; same shape
  as `shell.pickDirectory` blocking the worker on a modal.
- **Four stale-docstring flags, carried forward UNVERIFIED** from an earlier
  sweep: the `PermissionRequest` dataclass (`permissions/gate.py`),
  `ModelRouter.register` (`providers/router.py`), a claim in
  `openai_provider.py`, and `default_cloud_model([])`'s defensive gap
  (`models_catalog.py`). All four symbols still exist; whether the observations
  still hold has not been re-checked. Re-verify or delete the line.
- **Polish, unstarted:** no conversation search in the sidebar; **scoped consent
  ("always allow" per site)** — a SAFE grant is keyed by tool id, so after the
  first card every later `read_web_page` is ungated and model-addressed, with
  Activity-Panel visibility as the shipped mitigation; "Not now" sometimes
  described by the model as a malfunction; routine-save affordance is a small
  link in the activity strip.

**Opened by steps 4 + 5 — decide these, don't rediscover them:**

- **The webview cannot open an external link, at all.** `main.rs` registers three
  commands for it (`send_to_core`, `store_provider_key`, `delete_provider_key`);
  `shell.openExternal` is CORE→shell, reachable only by the `open_link` tool, and
  `Markdown.tsx` states the rule as "the webview must never open URLs itself, and
  must never call any `shell.*` IPC method". So every address shown in Settings is
  copy-paste text (the Google free-tier line now says so honestly), and
  `Markdown.tsx`'s inert anchors are inert for the same reason. If clickable links
  are wanted, the fix is **one narrow webview→shell Tauri command**, not an anchor
  — and it is new highest-trust surface, so it is an owner call, not a cleanup.
- **The Custom guard panel still has no workspace-trust guard**, which CLAUDE.md
  and this file both said step 5 would add ("as those capabilities land, never
  before"). It was not in the frozen step-5 contract, so it was not built. In the
  meantime the precedence question is answered defensively: `auto_grant_scope='none'`
  now beats trust (see rigor-pass item 6). **Decide at step 6 or 8** whether the
  panel grows the third guard or whether that precedence rule is the whole answer.
- **`tsc --noEmit` does not cover the test files** — `tsconfig.json` excludes
  `src/__tests__` and `*.test.ts(x)`. A fixture that drifts from the hook signature
  it drives is invisible to the typechecker; that is exactly how `useTurn.test.tsx`
  came to be missing a required callback. Consider a second `tsconfig.test.json` in
  CI; it is a real hole in a gate people trust.
- **`policy._canonical` case-folds unconditionally**, so `/tmp/PROJECT/x` is judged
  inside the trusted root `/tmp/project`. Correct on APFS/HFS+ default
  (case-insensitive), **wrong on a case-sensitive volume**, where it widens
  confinement. macOS-only assumption, currently undocumented in the function.
- **The floor protects Addison's DATA, not Addison's CODE.** A trusted root may
  contain the repo (fine — that IS the harness working for a developer) or, in a
  packaged install, `/Applications/Addison.app`, where the model could rewrite
  `policy.py` card-free. The amendment's "inviolable machinery: Addison's code and
  the global floors" is therefore broader than what ships. Either narrow the wording
  or add the running app's resource root to `_protected_dirs`. **Owner call.**
- **A hardlink inside a trusted root to a file outside it is trusted** — `realpath`
  cannot see hardlinks. Inherent to any realpath-based confinement; noted rather
  than fixed.
- **`workspace.pickDirectory` blocks the worker thread** on a modal dialog with the
  bridge's 60s ceiling; browse for longer and the timeout is swallowed into
  `{"directory": null}` with no explanation, while every other store RPC queues
  behind the open dialog.
- **A failed endpoint add still clobbers the keychain**: the card stores the key
  under `custom` before `confirmAddEndpoint`, so a failed connect leaves the new key
  overwriting any previous custom-server key, with no rollback and no disclosure.
  The ordering is contract-mandated and G1 is intact; the undisclosed clobber is not.

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
