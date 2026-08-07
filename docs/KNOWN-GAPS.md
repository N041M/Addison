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

- ~~The denylist's CONTAINS direction is scaffolding and should be deleted.~~
  **RETIRED WHERE THE KERNEL DOES THE JOB, 2026-08-06.** `ls ~`, `ls .`,
  `grep -r TODO .` and `npm run build -- --out .` were refused outright — not
  carded, refused — because `rm -rf ~` takes the G3 floor with it and read and
  write are not distinguishable in a `shell=True` string (#48, three times). The
  seatbelt makes that distinction at the kernel, which was this entry's own stated
  condition for removal, so `policy.command_denied_path` now skips the direction
  wherever writes are confined. INSIDE is untouched: the sandbox deliberately
  permits reads, so `cat ~/.ssh/id_rsa` is refused here or nowhere.
  **Retired by PLATFORM, not deleted** (`policy.kernel_confines_writes`) — where
  `sandbox_invocation` shells out to `/bin/sh` with `sandboxed: false`, this
  string is still the only thing between `rm -rf ~` and the recovery floor.
  It was closed without waiting for the `forbidden` audit data this entry asked
  for, and the reason is that the data would only have measured *frequency* while
  the argument turned on *correctness*: the refusal never protected the floor on
  macOS — the kernel did — it only refused to let the model try, and a control a
  developer cannot approve past is one they route around with `cd`, which also
  defeats the relative-path resolution. A verb-list classifier ("keep it for `rm`,
  drop it for `ls`") was rejected for the reason the docstring already gave: it is
  wrong in the permissive direction the first time someone writes `python -c`.
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
- **The permission card shows the command, not its consequences (open, 2026-08-06).**
  A card for `rm -rf build` says `rm -rf build`, which is the least informative
  true thing that could be shown. Two narrower forms of "preview before you
  approve" are open — both are cheaper than they sound and neither is the
  VM-dry-run idea [`ROADMAP.md`](../ROADMAP.md) rejects (that one runs a
  side-effecting command twice; these run nothing):
  - **Compute the affected set, execute nothing.** For a delete, walk the path
    and put the count on the card — "1,240 files, 3 modified today". No sandbox,
    no clone, no execution; it is a directory read. This is the one worth
    building, and it is a day rather than a subsystem.
  - **A copy-on-write clone for the file-only subset.** APFS `clonefile` is
    instant and free, so the command could run against a clone under the existing
    seatbelt with `network-outbound` denied, and the diff shown. Honest limits:
    it covers only commands that need no network, and it must SAY it skipped the
    preview rather than silently showing none.
  If ever scheduled this is **5.6**, not a new step — it is card and containment
  work on the step-5 harness, the same shape 5.5 was.
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
- The data-versus-code edge was **unchanged by this step and was then the sharper of
  the two** — the seatbelt profile denied writes to the data dir but not to a packaged
  `/Applications/Addison.app`. **That has since been closed for a packaged install
  (2026-08-06)**; what is left open is the wording, not the code. It is the same owner
  call opened by steps 4 + 5 and is stated once, below; it is not restated here.

**The keychain integration has a plan (2026-07-31), and its first two steps are
BUILT (2026-08-06):** [docs/secrets-and-keychain-plan.md](secrets-and-keychain-plan.md).
The double-password diagnosis first produced a ground-up encrypted-vault rewrite;
scrutiny (60 findings) and two spikes then **turned it into a repair-first plan**.
Steps 1 and 2 landed on 2026-08-06 — presence left the keychain for
`provider_config.secret_presence`, and every credential write is now an explicit,
verified delete-then-add with self-heal on top of it. **Two of step 4's four items
landed the same day**: a definitive 401/403 now marks the provider needs-attention
on a third column, `provider_config.key_rejected_at`, says one plain line ONCE, and
lets routing degrade to another connected provider (§5.2); and a key is normalised
and shape-checked where it is STORED, in `keychain.rs`, rather than trusted to the
frontend's `.trim()` (§5.3). **What is still PROPOSED**: `Intent` and the
background-caller re-arm (§4.3), launch reconciliation (§5.1), the shipped read
counter (§5.6), and the click-anchored cards (§6) — which is where a
needs-attention Settings ROW will live; today §5.2's state is core-side plus one
chat-side line. So one item on the old list remains true: G1's zeroization stops at
the Python boundary. The vault survives as a documented destination with named
triggers (step 7's MCP tokens, Android, or the Phase-3 identity rotation). §14 lists
the owner decisions; **decisions 3 and 6 are now answered** — see
[BUILD-LOG.md](BUILD-LOG.md).

**The presence probe cost is CLOSED (built 2026-08-06).** It had been watched
happening on 2026-08-01: with `ADDISON_KEYCHAIN_TRACE=1`, `_primary_key_available()`
(`main.py`) showed up as a real OS keychain read, because **the probe IS the
keychain read**, and it ran on polls with no user action behind it — roughly ten
undismissible dialogs stacked in one session, each orphaned when the app restarted.
Presence is now a SQLite column and no polled or launch-driven path reads a key to
answer it; the `_connections` / `_provider_list` fallbacks and the server's
`_primary_key_available` are gone. What is left is deliberate and person-driven: the
per-turn read (`_primary_key_status`, still fresh, because it is the one caller with
a person behind it), `provider.connect`, and the post-restore keyless note. The one
caller class NOT yet fixed is the background pair the plan's §4.3 owns —
`_maybe_load_live_catalog` and `_maybe_reconnect_saved_providers` still fetch a key
value without a person behind them, which is why `FAILED_READS` survives in
`keychain.rs` as a decline memory (§5.5) rather than being deleted with the poll.

**Self-heal does NOT cover the device-identity item, deliberately (2026-08-06).**
The plan's §4.2 says self-heal "applies to provider keys and the device-identity
item alike". Only the provider keys got it. The reason is the asymmetry §7 of the
plan already names: **a provider key can be pasted again from the vendor's website;
the device identity's private half can be recovered by nobody.** Self-heal is a
delete-then-add, and delete-then-add is the one operation in this subsystem that can
lose data — so running it against the single irreplaceable secret needs its own
verification pass, not a shared one. Consequence, stated plainly: on a build whose
signing identity has rotated, the device item stays foreign and keeps costing one
dialog per session, which is exactly the symptom self-heal exists to end. Doing it
would mean at minimum a read-back verification that reconstructs and test-signs with
the restored key before the old item is trusted as replaced, plus a decision about
what the app should DO if the identity is lost (it cannot tell: it would mint a fresh
one and the relay would see a brand-new device). Follow-up item, owner call.

**A stable signing identity was NOT enough to make "Always Allow" stick, and the
reason is worth keeping.** `sign-and-run.sh` was written on the premise that
signing every dev build with one identity gives the keychain ACL something
durable to match. That premise is necessary and was not sufficient: asked to
invent a designated requirement for a **self-signed** leaf, `codesign` falls
back to `cdhash H"…"` — a hash of the binary's CONTENTS — and macOS stores THAT
as the ACL entry. Measured on this repo: a correctly-signed build still carried
`designated => cdhash H"1380cf87…"`, so every rebuild presented a new
requirement and the granted permission could never match. That is the original
ad-hoc bug wearing a certificate. Fixed by naming the requirement explicitly
(`identifier "addison" and certificate leaf H"<cert>"`), read from the keychain
rather than hard-coded so it does not silently regress on another clone. Kept
here rather than only in the script because the failure looks exactly like a
user error — pressing the button and having nothing happen — and cost real time
twice.

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
- **MCP tools in SAFE — still open, but it no longer BLOCKS step 7.** Read-only
  only, a curated allowlist, or dev-only? And how MCP tool metadata declares
  undo-ability. **A server declares its own risk, so this cannot be taken on
  trust** — see the sharpened note in the spec's MCP section and item 4 of the
  step-5.5 plan. What unblocked the step was the owner's 2026-08-06 decision that
  MCP is **dev-only for v1**: SAFE admission is deferred rather than answered, and
  no code depends on it (phase 1 landed the same day and registers nothing at
  all). Promoting a tool into SAFE is a later, separate decision.
  [step-7-mcp-plan.md](step-7-mcp-plan.md) owns the step's phases and its other
  decisions — **transport was the second open question and is now answered: HTTP
  only for v1**, which is why nothing in the step launches a program.
- ~~**Widget capability tiers and vocabulary (blocks step 6).**~~ **CLOSED
  2026-08-06.** The safe interactive kinds are `checklist`, `note` and `timer`, and
  the vocabulary is a **closed, hard-coded set** — a widget spec does NOT declare
  the capabilities it needs, and there is no capability→mode map, because the list
  of kinds is the gate (`agent_core/widgets.py`; [SAFETY.md](SAFETY.md) owns
  invariant 4). Where a widget invokes a tool, the tier check is
  `registry.visible_tools(mode)` and never a second risk model. Code-backed widgets
  are still Developer-only and still unbuilt; when they land they are listed by the
  same `widget.list`, disabled in Simple like every other dev-made artifact.
- **A routine's availability is still decided by its STAMP, not by what it needs.**
  The widget half of this was fixed on 2026-08-06 (`widget_uses_dev_abilities`,
  [SAFETY.md](SAFETY.md)); routines have the identical bug and it is **worse there,
  because it reaches dispatch.** `builder.save` stamps `created_in_mode=mode.value`
  unconditionally, so a routine of nothing but `web_search` steps, saved while
  Developer was active, is stamped `open` — then listed disabled in Simple
  (`rpc/routines.py`) *and refused outright* by `_handle_routine_run`, which tests
  `created_in_mode(routine_id) == 'open'`. `routine_uses_dev_abilities` already
  exists and is the right question; it is used only for the **save-time** refusal
  in `builder.py`, never for availability.
  Two things make this an owner call rather than a follow-on commit. **It loosens a
  dispatch refusal in SAFE**, which is invariant-adjacent: the argument that it is
  safe is that the engine's per-step `dev_only` check is the real enforcement and a
  command-free routine replays through `visible_tools(SAFE)` with the gate carding
  per invocation (invariant 3) — sound, but it should be *decided*, not inherited
  from a widget fix. And **the correct test is not `routine_uses_dev_abilities`
  alone**: that only looks for `step.command`, so a step naming an `open_only` tool
  (`read_project_file` / `write_project_file`) needs Developer and would not be
  caught. The real test needs the registry as well as the plan, so it belongs in
  the RPC layer — the module boundary rule keeps `routines/` from importing
  `tools/`. Until it lands, `rpc/widgets.py::_widget_needs_dev` deliberately reads
  the routine's stamp for its look-through, so the rail and the library cannot
  disagree about the same routine; that is the one line that follows this fix.
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
- ~~**Three stale-docstring flags, still UNVERIFIED.**~~ **All three resolved
  2026-08-06 — one was real, two were the stale thing.** `openai_provider.py` was
  REAL and is fixed: its module docstring said the custom base URL is "validated
  http(s):// at connect time (main.py)", and that validation is
  `rpc/providers.py::_valid_http_url` — the RPC split moved it and the reference
  did not follow. `ModelRouter.register` (`providers/router.py`) is **accurate**:
  it names `DirectAPIProvider`, which exists (`providers/direct_api_provider.py`),
  and `register` really is additive per role. The `PermissionRequest` dataclass
  (`permissions/gate.py`) **has no docstring at all**, so there was never anything
  there to be stale. Both flags deleted rather than re-verified again — a flag that
  survives two checks against a thing that does not exist is itself the defect. The
  fourth,
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
  *(Step 6 shipped 2026-08-06 without touching it — it turned out to be entirely
  widget-side — so this is step 8's decision now, or an owner call before it.)*
- ~~`tsc --noEmit` does not cover the test files.~~ **CLOSED 2026-08-01.**
  `shell/tsconfig.test.json` + an `npm run typecheck` script that runs both
  configs. Every error it found on the first run was real, in four classes,
  including the exact failure this entry predicted: a `ConversationSummary` fixture
  carrying a `messageCount` field the type has never had. The other three were
  unchecked `normalizeProfile` nulls (now a narrowing helper that says why null
  would be a parser bug), an `afterEach` returning `VitestUtils` instead of void,
  and a `vi.fn(() => { throw })` inferring `Mock<[], never>`.
- **`policy._canonical` case-folds unconditionally**, so `/tmp/PROJECT/x` is judged
  inside the trusted root `/tmp/project`. Correct on APFS/HFS+ default
  (case-insensitive), **wrong on a case-sensitive volume**, where it widens
  confinement. macOS-only assumption, currently undocumented in the function.
- ~~The floor protects Addison's DATA, not Addison's CODE.~~ **CLOSED FOR A
  PACKAGED INSTALL, 2026-08-06.** *(This is the single statement of it; SAFETY.md,
  design-doc §9.x and HANDOFF all point here.)* In a packaged install the model
  could rewrite `policy.py` inside `/Applications/Addison.app` card-free, which is
  a more complete bypass than deleting the snapshots ever was. The running app's
  BUNDLE now joins the protected set (`filesystem.rs::addison_app_bundle`), so the
  seatbelt denies writes to it exactly as it denies the data dirs — one mechanism,
  not a second one beside it.
  **A dev build contributes nothing, deliberately**, and that is not a gap: the
  dev binary lives in the repo, and that repo is exactly what the coding harness
  is FOR when the person using it is the developer working on Addison. Denying it
  would break the harness's most legitimate use to stop a threat that only exists
  once the code ships read-only. Detection is structural
  (`…​.app/Contents/MacOS/…`), never a guess from the binary's name.
  What remains open is the wording, not the code: the amendment's "inviolable
  machinery: Addison's code and the global floors" is still broader than what
  ships, because a *developer's* checkout is writable by design.
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
- ~~**`primary.txt` widget guidance says Addison can't build custom-app widgets.**~~
  **Rewritten 2026-08-06 with step 6 half A**, which is what it was waiting for: the
  guidance now names the checklist, note and timer as things Addison really makes,
  states the two limits worth hearing early (a checklist's lines are fixed at
  creation; a timer never rings, because nothing runs by itself), and keeps the
  refusal for what is still not a widget — a calculator, a game, a watcher. The
  never-save-a-file-instead rule is unchanged and still load-bearing. It remains
  MITIGATION, not a mechanism: it failed once (#43) and was re-hardened once (#45),
  and a third regression should go structural — a registry-level guard on
  `save_file` calls that look like widget substitutes.
- **The design-doc and engineering-spec *bodies* predate the SAFE/OPEN
  mode-scoped model and have no widgets section.** They carry amendment banners
  and precedence notes, but a dedicated reconciliation pass would be worthwhile.
