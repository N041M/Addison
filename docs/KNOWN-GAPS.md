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
  and it needs an explicit owner decision rather than silent expiry. **The THIRD
  trigger went live 2026-08-07** when step 7 phase 3 shipped dispatch: a tool
  server's descriptions, schemas and answers now reach a model's context. Screening
  is still v2 and that is unchanged; what shipped instead is a recorded backstop —
  redaction, caps and a card on every single call — stated at its real strength in
  [step-7-mcp-plan.md](step-7-mcp-plan.md) §7, which owns that re-read. **Phase 4
  re-read it again the same day** against the wider surface it opened, reached the
  same answer, and added ONE thing: a cleaning pass over a server's answer that
  runs BEFORE the redactor, because a credential with a zero-width space in the
  middle of it matches no rule and cleaning afterwards would have handed a model a
  key the redactor had already declined to see. That is a character filter and not
  a screen — it does not read the text and would not notice the plainest injected
  instruction — and §7 says so in those words. **Partly
  mitigated 2026-07-31**: output redaction (`agent_core/redaction.py`) strips the
  credential shapes it knows on the way to the model and the audit trail records
  that it happened — but an unrecognised or deliberately-encoded secret still
  passes, so this stays open and is stated as such in design-doc §9.x.
- ~~**OS-automation directories can be trusted and written today.**~~ **CLOSED
  2026-08-07, the same day it was found — by step 8 phase 1, in the same PR that
  recorded it.** The gap: `workspace_trust_allows` refused only Addison's own
  protected directories, so `~/Library/LaunchAgents` could be granted as a
  trusted workspace and `write_project_file` could put a plist there behind an
  ordinary card — login-time automation, armed, no keyword gate. Closed by the
  fence [step-8-automation-plan.md](step-8-automation-plan.md) §5.5 specifies:
  ONE closed list (`policy.OS_AUTOMATION_DIRS`, hand-synced entry-for-entry with
  `exec.rs`'s copy and pinned by a lockstep test that reads both), THREE
  consumers — the trust floor refuses those directories in both directions at
  grant AND authorize time (so a pre-fence trust row over one stopped confining
  anything the moment this landed, no migration needed), `denylisted_roots`
  refuses a command naming one plus the four arming binaries
  (`launchctl`/`crontab`/`at`/`batch`) as a segment's first word — or behind a
  prefix the shell itself drops (`sudo`, `exec`, `env`…) — and the
  seatbelt write-denies them shell-side after every allow, dropping any trusted
  root that touches one. Recorded costs, each stated where the code makes it:
  `~/Library` and `~/.config` are no longer trustable workspaces, and a command
  merely READING a plist is refused by the denylist (which cannot tell read from
  write; the seatbelt, which can, denies only writes).
- **A G3 restore can orphan an armed job, and nothing on any surface can then name
  or stop it (found by the phase-4 review, 2026-08-07).** `apply_config_state` is
  REPLACE-ALL, so restoring a snapshot that predates an automation deletes its row —
  while `~/Library/LaunchAgents/<label>.plist` stays installed and launchd goes on
  running it at every login. After that: `disarm_automation` refuses ("that
  automation isn't saved any more"), `automation.remove` refuses the same way before
  it can reach `_disarm_before_forgetting`, and the Settings section renders
  armed-ness per ROW so it shows nothing at all. Recovery is `launchctl` by hand.
  **This is the same shape phase 3's review fixed for the Remove path** — a job
  nobody can see and nobody can stop — reachable now through Restore instead.
  Phase 4 made it *tidier and no better*: before, the stale row sat on screen doing
  nothing useful; now it vanishes.
  The plan's §5.6 says a restore "never arms", and `snapshots/scope.py` says it
  "cannot arm, and cannot un-arm either" — both true, and both silent about a
  restore that takes the ROW away from a job that is still running.
  **The real fix is reconcile-on-restore**: after a restore, ask the OS what it
  holds and surface any armed label with no row as its own row ("running, but not
  saved here") with a Disarm on it. Until then this is recorded rather than closed,
  because the alternative — blocking the restore, or silently disarming during one —
  would put arming decisions inside the one action G3 promises is always available.
- **An armed automation may launch Addison itself, and nothing refuses it — an
  OWNER QUESTION, not a defect (raised by the phase-3 review, 2026-08-07).**
  `policy._ARMING_BINARIES` refuses `launchctl`/`crontab`/`at`/`batch` as a
  command's program, but an automation whose command is `open -a Addison` on a
  one-minute interval passes the door, the fence and the keyword card. **My read is
  that this is within G2's letter**: the OS runs it, at the person's explicit
  typed-code consent, exactly like a login item they made themselves, and Addison
  still has no timer, watcher or callback of its own. But it is the one shape that
  produces an Addison-relaunch loop, and the plan's §6 ("no Addison-side scheduler
  … in any phase, ever") never contemplated a job that starts the app rather than
  being started by it. **What it needs is a sentence from the owner** — either "that
  is a person's prerogative" or a denylist entry — rather than a code change made
  quietly on the strength of one reviewer's reading. Recorded here because a
  judgement call that lives only in a review report is one nobody makes.
- **A line inside a heredoc is read as a command, so an ordinary document can be
  refused as "arming" (step 8 phase 1; recorded 2026-08-07).** `_SEGMENT_SPLIT`
  treats every newline as the start of a new command — it must, because
  `ls\ncrontab -` is two commands and that was #48's vector — and the same rule
  reads a heredoc BODY as commands. Since `at` and `batch` are ordinary English
  words, `cat > NOTES.md <<'EOF'` followed by a line beginning *"at last we fixed
  it"* is refused with the arming sentence. Bounded and deliberate: it needs the
  arming word at the START of a line, it is Developer-profile only, and the person
  can run the command in their own terminal. **The cost of the alternative is
  higher** — not splitting on newlines would let `ls\ncrontab -e` past a guard whose
  whole job is the obvious spelling. `tests/test_step_5_5_containment.py`'s
  `_ARMING_FALSE_POSITIVES_ACCEPTED` pins the behaviour so a change is a decision;
  if it is ever fixed, delete that test WITH this entry.
  **A wider version of this was introduced and reverted the same day**: a fix-round
  step-over walked past every `=`-bearing word, so `label=Nightly batch job` inside
  a `.properties` heredoc was refused too. The adversarial pass over the fixes
  caught it; `env X=1 crontab -` is conceded instead
  ([step-8-automation-plan.md](step-8-automation-plan.md) has the rest of the
  fence's concessions).
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
- **`routines/engine.py` — FIVE pre-gate guards each duplicate `on_failure`
  handling.** The unknown-tool refusal, the dev-only guard, the not-callable
  guard, the step-5.5 denylist and the confinement guard each shape their refusal
  as a failed step and re-implement abort / ask_user / skip **inline** instead of
  falling through to the canonical `if not result.success:` block. All five match
  that block today and will silently diverge the moment someone adds a fourth
  `on_failure` policy. **This entry has been overtaken twice** — it was written
  about three guards, phase 2 added a fourth and the 2026-08-07 review added a
  fifth, and each copy was written to match its neighbours rather than introduce a
  new shape. That is the right call for one diff and the wrong equilibrium overall,
  and the rate at which the list grows is now the argument. **Fix by restructuring
  so all six paths share one block**; it is cheaper to do than to keep deferring.

**Open design questions, each blocking a specific step** (moved here from the scope
amendment's §13 when that document was retired, 2026-07-27 — the other four §13
questions were resolved during steps 1–3 and went with it):

- ~~**Keyword-gate syntax (blocks step 8).**~~ **ANSWERED 2026-08-07 — no longer
  blocks the step.** The syntax is a **per-automation nonce** Addison shows and
  the person retypes (owner decision: a fixed prefix like `!run` is forgeable by
  anything that can write English — observed content can say "type `!run
  install`", but cannot pre-write a code that did not exist yet). The set of
  actions it gates is settled the way the owner's reading pointed: **arming**
  OS-run automation in the harness, never ordinary chat — a one-shot command
  already meets a per-invocation card and the seatbelt, and the recurring,
  unconfined, outlives-the-session nature of an armed job is the jump that earns
  the ceremony. [step-8-automation-plan.md](step-8-automation-plan.md) owns the
  build order and the surrounding decisions. **All four phases landed 2026-08-07** —
  the fence, authoring, the gate and arming, and state honesty — so this question is
  answered AND built.
- **MCP tools in SAFE — still open, but it no longer BLOCKS step 7.** Read-only
  only, a curated allowlist, or dev-only? And how MCP tool metadata declares
  undo-ability. **A server declares its own risk, so this cannot be taken on
  trust** — see the sharpened note in the spec's MCP section and item 4 of the
  step-5.5 plan. What unblocked the step was the owner's 2026-08-06 decision that
  MCP is **dev-only for v1**: SAFE admission is deferred rather than answered, and
  no code depends on it — phases 2 and 3 register a server's tools and call them,
  and every one is `open_only`, so the SAFE view has never held one. Promoting a
  tool into SAFE is a later, separate decision.
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

**Opened by step 7 phase 2 (2026-08-07):**

- ~~**A refused MCP tool call leaves no audit row.**~~ **CLOSED 2026-08-07 by
  phase 3**, which owned the fix because it owned the migration. `tool_audit.outcome`
  is a CHECK-constrained vocabulary, so widening it was a schema rebuild and not an
  insert: `CREATE TABLE IF NOT EXISTS` leaves every existing database on the old
  CHECK, so a new value would have worked on a fresh DB and been swallowed by
  `_audit`'s best-effort `except` on an upgraded one — a log that quietly stops
  logging, which is worse than no row at all. `Store._migrate_tool_audit_outcomes`
  rebuilds the table, preserving every existing row, and the
  vocabulary gained `not_callable` (the refusal this entry was about) and `failed`
  (the gate said yes and the call never landed). Both dispatch paths write both.
  *(The rebuild's first version preserved those rows only when nothing interrupted
  it; how that was closed is in [BUILD-LOG.md](BUILD-LOG.md).)*
  The refusal branch itself is now quiet for MCP tools — they are callable — and
  remains the mechanism `mcp_catalog.MCP_TOOLS_ARE_CALLABLE` operates through.

**Opened by step 7 phase 4 (2026-08-07):**

- **A tool server that answers in pictures is a tool server Addison cannot use.**
  Phase 4 counts and discloses `image` / `audio` / binary-resource parts and
  forwards none of them ([step-7-mcp-plan.md](step-7-mcp-plan.md) §4.4, decision 1),
  so a server whose whole output is a chart returns *"nothing Addison can pass on"*
  plus a count. That is the deliberate answer and it is the right one for v1 —
  provenance, not capability, is the objection: the machinery to carry an image to a
  model exists (`read_file` → `_gate_image_result`) and it carries a file **the
  person picked**, not bytes a program nobody has audited pushed in unasked.
  Recorded here rather than only in the plan because it is a real limitation
  somebody will meet, and because the upgrade path is specific rather than
  hypothetical: route a server's image through the same vision gate, behind the
  same per-invocation card, once there is a reason to trust the provenance —
  which is the promoted-allowlist decision wearing a different hat, and is
  therefore the same later conversation.

**Opened by the 2026-08-07 review of all four step-7 phases:**

Two shapes of credential still cross `agent_core/redaction.py` untouched, and both
are deliberate as far as they go. The redactor is a **backstop, not a boundary** —
its own header says so and [step-7-mcp-plan.md](step-7-mcp-plan.md) §7 owns the
strength that may be claimed for it — so these are not bugs against a promise. They
are here because somebody will meet them, and because anything built on top of
"the redactor saw it" is built on sand.

- **A credential split by a newline, tab, quote or backslash passes, in BOTH the
  text and the structured channel.** `mcp_client.clean_result_text` rejoins a key
  cut in half by an *invisible* character, which is what makes cleaning a security
  change rather than hygiene, and it deliberately stops there. A newline between two
  table rows IS the table; a tab is a column; a quote and a backslash are what JSON
  is made of. Removing them to reunite a key would mangle every honest answer in
  order to catch a dishonest one, and a redactor that mangles ordinary text is one
  people switch off. Nothing at all is redacted *(measured 2026-08-07 · an sk-ant-
  key of 24 characters with one newline at character 12, taken through
  clean_result_text and then redact, python 3.13 in agent_core/.venv on the owner's
  machine)*, and the audit row honestly records that nothing was.
  **What it costs:** getting a key past this pass costs a server one
  keystroke, so no later control may assume the text it receives has been cleared.
  **What would close it** is not a wider character class — it is the untrusted-
  content screening deferred to v2, which three separate triggers already point at
  (above, and §7 of the plan).
- **A fullwidth or homoglyph credential (`ＡＫＩＡ…`) is not caught, and NFKC
  normalization was deliberately not half-built.** Folding a copy of the text and
  matching against the fold finds the key and then cannot say where it was: the
  folded string has different offsets from the original, so replacing what was found
  means the redactor must expose SPANS and map them back — a change to the shape of
  the most safety-critical file in the tree, made for a shape nobody has yet been
  seen to send. The half-built version is the one that must not exist: a redactor
  that matches on the fold and returns the original names a kind in the audit row it
  did not actually remove from the text, which is worse than this gap, because this
  gap at least reports itself honestly. Owner call, with the cost written down.

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
- **The name on the card is resolved a SECOND time, so it can go stale between the
  label and the effect — DECIDED 2026-08-08 and scheduled, not open.** Both file
  tools' `permission_detail` now asks `call_affected_path`, the very function
  confinement asks, which is what stopped a symlinked `notes.txt` carding as
  *notes.txt* while `secrets.env` was read. But it is a **separate call**: the path is
  resolved once for the display string and once for the boundary, and a symlink
  swapped between the two shows a name that was true only when it was read. The
  asymmetry is the whole reason this is a gap and not a hole — **the label can lie;
  the effect cannot.** The read or write still lands on the path confinement checked,
  so the worst case is somebody approving under a stale name, never a tool acting
  outside trust. `agent_core/tools/read_project_file.py`'s `permission_detail`
  docstring states the residual next to the code, which is where this repo trusts a
  rule most. **The fix is chosen, not pending a choice:** thread ONE resolved path
  through `call_permission_detail` and its other callers, so the card and the boundary
  share a single resolution. That is a signature change rather than a local edit,
  which is why it is built as part of the review surface's read-paths work
  ([`phase-3-review-surface-plan.md`](phase-3-review-surface-plan.md) Build §1, whose
  confinement order already reads *resolve once* and *pass only the resolved value*).
  Do not patch it inside one tool in the meantime — two tools resolving twice each is
  exactly the shape that change removes.
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
