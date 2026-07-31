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

**Opened by step 5.5 items 1–3 (2026-07-31):**

- **The denylist's CONTAINS direction is now scaffolding, and should be deleted.**
  `ls ~`, `ls .` and `ls /` are refused outright, not carded, because `rm -rf ~`
  takes the G3 floor with it and read/write are not distinguishable in a
  `shell=True` string (#48, three times). **The seatbelt profile now makes exactly
  that distinction at the kernel** — `rm -rf ~` fails, `ls ~` would succeed — so
  the direction has outlived its reason. Deleting it means dropping the
  `_names_a_directory` branch in `policy.command_denied_path` and the
  `_MUST_BE_FORBIDDEN_CONTAINS` list. Not done in the same change as the sandbox
  on purpose: removing a refusal on the same day the thing that replaces it lands
  is how a gap opens. **Item 4's audit log now exists (2026-07-31), so the
  mechanism is in place — what is missing is DATA.** A `forbidden` row now records
  every time this fires; revisit once real use has produced some, rather than
  guessing at the frequency.
- ~~A forbidden call is invisible outside the transcript.~~ **CLOSED 2026-07-31**
  by item 4's `tool_audit`: every refusal writes a row with `outcome='forbidden'`,
  at all three dispatch sites. The same change closed the older hole it was
  grouped with — `read_web_page` is LOW so it writes no `action_snapshots` row, and
  the tool most exposed to prompt injection now leaves a durable record of which
  hosts it reached (`detail` is the host, never the full URL).
- **A command runs UNCONFINED on any platform without a profile.** macOS refuses
  rather than running bare; Linux has no Landlock/bubblewrap path yet, so the
  command runs and the answer carries `sandboxed: false`, which the tool prints
  above the output. Never silent — but never protected either, and v1 is macOS, so
  this is a real gap the day a second platform ships.
- **`sandbox-exec` is formally deprecated by Apple.** It still works and is what
  Claude Code and Codex CLI both rely on. Acceptable; not permanent. **Recorded in
  design-doc §9.x (2026-07-31)**, so it is documented rather than rediscovered —
  the gap is now the dependency itself, not the silence about it.
- **A sandboxed command can reach the network, deliberately.** `network-outbound`
  is granted; `network-bind` is not. Denying outbound was the first draft's
  accidental default and it broke `git fetch` / `npm install` / `pip install`
  while buying nothing — the command's output already travels to a cloud provider,
  so blocking `curl` closes only the useful half. **This makes item 4 (output
  redaction) and the v2 untrusted-content screening deferral load-bearing rather
  than theoretical**: they are now the only things standing between a prompt-
  injected command and a deliberate exfiltration. The CLAUDE.md deferral for
  screening was written with a trigger ("becomes load-bearing once free/gray-area
  endpoints and MCP tools are in play") — this is a second trigger arriving early,
  and it needs an explicit owner decision rather than silent expiry. **Partly
  mitigated 2026-07-31**: output redaction (`agent_core/redaction.py`) strips the
  credential shapes it knows on the way to the model and the audit trail records
  that it happened — but an unrecognised or deliberately-encoded secret still
  passes, so this stays open and is stated as such in design-doc §9.x.
- **Trusted roots reach the shell as data on every call.** `writeRoots` is sent by
  the core, so the profile is only as narrow as that list. The shell re-derives
  and re-denies its own data dirs on top, independently, which is what keeps the
  floor from depending on the core's honesty — but a *widened* allowlist is not
  independently checked. Nothing in the tree can widen it today (it is read
  straight from `workspace_trust`); noted so the next thing that touches that path
  knows what it is standing on.
- **The floor still protects Addison's DATA, not Addison's CODE.** Unchanged by
  this step and now the sharper edge of the two: the profile denies writes to the
  data dir, not to a packaged `/Applications/Addison.app`. See the existing
  owner-call item below — this is the natural moment to close it, since `exec.rs`
  is where the extra deny would go.

**The keychain integration has a plan (2026-07-31):**
[docs/secrets-and-keychain-plan.md](secrets-and-keychain-plan.md). The
double-password diagnosis first produced a ground-up encrypted-vault rewrite;
scrutiny (60 findings) and two spikes then **turned it into a repair-first
plan** — presence moves out of the keychain into the existing `provider_config`
table, foreign items self-heal by delete-and-recreate, and `Intent` replaces the
probe zoo. The vault survives as a documented destination with named triggers
(step 7's MCP tokens, Android, or the Phase-3 identity rotation). Also new and
independent of either path: a 401 currently changes nothing (a revoked key fails
every turn forever), keys are trimmed only in the frontend, and G1's
zeroization stops at the Python boundary — all three are written up in §5.
**PROPOSED, not scheduled**; §14 lists the owner decisions.

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
- **`routines/engine.py` — THREE pre-gate guards each duplicate `on_failure`
  handling.** The dev-only guard, the confinement guard and (since 2026-07-31)
  the step-5.5 denylist each shape their refusal as a failed step and
  re-implement abort / ask_user / skip **inline** instead of falling through to
  the canonical `if not result.success:` block (~L255). All three match that
  block today and will silently diverge the moment someone adds a fourth
  `on_failure` policy. The denylist copy was written to match its neighbours
  rather than introduce a fourth shape — which is the right call for one diff and
  the wrong equilibrium overall. **Fix by restructuring so all four paths share
  one block**; it is now cheaper to do than to keep deferring.

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

- ~~`RoutineLibrary` shares one `values` map across routines.~~ **CLOSED
  2026-08-01.** `values` is now scoped by a `valuesFor` routine id and only sent
  to the routine it was entered for. **The repro in this entry was wrong and the
  fix is narrower than it looked:** `executeRun` clears `values` in its `finally`,
  so *completing* routine A cleans up after itself. The reachable path is
  **abandoning** a fill — open A's fill panel, type an answer, then run B (which
  needs no input, so it skips the fill step and runs immediately) and B carries
  A's answer under the shared name. Mutation-proven; a first version of the test
  passed under mutation because it ran A to completion first.
- **Empty-text `sendMessage` has no guard.** `_run_send_message`
  (`agent_core/rpc/conversation.py`) reads `params.get("text", "")` and never
  checks it; the CLI does. An empty message persists a blank user turn the
  rollback doesn't remove. Unreachable through the composer today — decide.
- **Local-setup pre-flight HTTP runs on the read loop.**
  `_handle_start_local_setup` (`agent_core/main.py`) is an inline dispatch handler
  and calls `is_running()`, which can block frame delivery up to 5s.
  `availableRoles` was moved off the read loop for exactly this reason; same shape
  as `shell.pickDirectory` blocking the worker on a modal.
- **Three stale-docstring flags, still UNVERIFIED**: `ModelRouter.register`
  (`providers/router.py`), a claim in `openai_provider.py`, and the
  `PermissionRequest` dataclass (`permissions/gate.py` — checked 2026-07-31: it
  has no docstring at all, so there is nothing there to be stale; the flag itself
  looks like the stale thing). Re-verify or delete the line. The fourth,
  **`default_cloud_model([])`, was real and is CLOSED 2026-08-01**: its docstring
  called `catalog[0]` "a safe fallback" while an empty catalog raised
  `IndexError`. It now raises `ValueError` naming the cause. No caller can reach
  it today (all three guard first), so this is for the next one — an empty live
  catalog fetch should say what went wrong, not surface three frames away.
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
- ~~`tsc --noEmit` does not cover the test files.~~ **CLOSED 2026-08-01.**
  `shell/tsconfig.test.json` + an `npm run typecheck` script that runs both
  configs. It found **nine real errors on the first run**, including the exact
  failure this entry predicted: a `ConversationSummary` fixture carrying a
  `messageCount` field the type has never had. Also fixed: five unchecked
  `normalizeProfile` nulls (now a narrowing helper that says why null would be a
  parser bug), an `afterEach` returning `VitestUtils` instead of void, and a
  `vi.fn(() => { throw })` inferring `Mock<[], never>`.
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
