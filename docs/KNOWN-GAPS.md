# Known gaps and open design questions

**This file owns the live-issue register.** Everything Addison knows to be
incomplete, undecided, or deliberately deferred is here; nothing else in the tree
keeps a second list. `VERIFICATION.md` and `HANDOFF.md` both used to, and both
drifted from this one.

Nothing here is a bug report against shipped behaviour: these are tracked gaps,
deliberate deferrals and decisions waiting on the owner. A green test run does not
close any of them. Defects with a known wrong behaviour live in
[`KNOWN-BUGS.md`](../KNOWN-BUGS.md) (added 2026-08-09, from the whole-app test
pass). That file holds the repros; this one keeps the design questions a defect
sometimes raises.

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

- **No transition.** The beside rail animates four properties on collapse
  (`width .35s, opacity .25s, margin-left .35s, transform .35s`), and the brief asks
  for exactly that (`design-brief-dark/README.md`: "Sidebar collapses via header «
  chevron (width/opacity/translate animate .35s)"). The inline form is
  conditionally *mounted*, so it pops in and out with no transition at all. Two
  affordances driven by one button behave visibly differently.
- **It lands in the wrong place.** It renders in `ChatThread`'s `footer`, so it sits
  in the reading column between the last message and the composer, which is why it
  read as "covering the chat window" even when short. It pushes the conversation up
  and competes with the composer for the eye.
- **The placement has no design authority behind it.** `IMPLEMENTATION.md` says
  "**Mobile** (<md): keep the existing drawer + inline-widgets structure", which
  covers below 768px only. The 768–1024 band was invented by the 07-26 responsive
  work by extrapolating the mobile rule; the brief never specifies it. Worth
  deciding rather than inheriting.
- **Prior art that was deleted:** `BottomSheet.tsx` was removed in the v4 cleanup as
  orphaned "since widgets moved inline on mobile". A sheet or slide-over anchored to
  the header button, sibling to the existing `MobileDrawer`, is the obvious
  candidate, and it would fix the transition and the position together. Check git
  history before rebuilding it from scratch.

**The sidebar's "Code" row carries no mono hint, and its two siblings do (UI/UX,
owner call).** `design-brief-dark/IMPLEMENTATION.md` describes the Workspace block as
rows whose right-hand mono fact is "a real count (or the policy mode); never a
placeholder": Tools shows the trusted-root count or the mode, Snapshots shows the
restore-point count. `Code` (shipped 2026-08-08, Developer/Custom only) shows
nothing, so one row in a three-row block has an empty right column. Either is
defensible and this is not a bug: the honest candidate hint is the number of edits
still live on disk, which is exactly `workspace.listEdits`'s count and already
fetched by `useCodeReview`; the honest argument against is that a hint reading `0`
next to a screen whose whole job is "show me what changed" is noise. Decide it as
design, not as a fix: `Sidebar.tsx` renders the row and `IMPLEMENTATION.md` owns
the block's look.

**Opened by step 5.5 items 1–3 (2026-07-31):**

- ~~The denylist's CONTAINS direction is scaffolding and should be deleted.~~
  **RETIRED WHERE THE KERNEL DOES THE JOB, 2026-08-06.** `ls ~`, `ls .`,
  `grep -r TODO .` and `npm run build -- --out .` were refused outright (not
  carded, refused) because `rm -rf ~` takes the G3 floor with it and read and
  write are not distinguishable in a `shell=True` string (#48, three times). The
  seatbelt makes that distinction at the kernel, which was this entry's own stated
  condition for removal, so `policy.command_denied_path` now skips the direction
  wherever writes are confined. INSIDE is untouched: the sandbox deliberately
  permits reads, so `cat ~/.ssh/id_rsa` is refused here or nowhere.
  **Retired by PLATFORM, not deleted** (`policy.kernel_confines_writes`): where
  `sandbox_invocation` shells out to `/bin/sh` with `sandboxed: false`, this
  string is still the only thing between `rm -rf ~` and the recovery floor.
  It was closed without waiting for the `forbidden` audit data this entry asked
  for, and the reason is that the data would only have measured *frequency* while
  the argument turned on *correctness*: the refusal never protected the floor on
  macOS (the kernel did); it only refused to let the model try, and a control a
  developer cannot approve past is one they route around with `cd`, which also
  defeats the relative-path resolution. A verb-list classifier ("keep it for `rm`,
  drop it for `ls`") was rejected for the reason the docstring already gave: it is
  wrong in the permissive direction the first time someone writes `python -c`.
- ~~A forbidden call is invisible outside the transcript.~~ **CLOSED 2026-07-31**
  by item 4's `tool_audit`: every refusal writes a row with `outcome='forbidden'`,
  at all three dispatch sites. The same change closed the older hole it was
  grouped with: `read_web_page` is LOW so it writes no `action_snapshots` row, and
  the tool most exposed to prompt injection now leaves a durable record of which
  hosts it reached (`detail` is the host, never the full URL).
- **A command runs UNCONFINED on any platform without a profile.** macOS refuses
  rather than running bare; Linux has no Landlock/bubblewrap path yet, so the
  command runs and the answer carries `sandboxed: false`, which the tool prints
  above the output. Never silent, but never protected either, and v1 is macOS, so
  this is a real gap the day a second platform ships.
- **`sandbox-exec` is formally deprecated by Apple.** It still works and is what
  Claude Code and Codex CLI both rely on. Acceptable; not permanent. **Recorded in
  design-doc §9.x (2026-07-31)**, so it is documented rather than rediscovered;
  the gap is now the dependency itself, not the silence about it.
- **The permission card shows the command, not its consequences (PARTLY CLOSED
  2026-08-13; the clone form is still open).**
  A card for `rm -rf build` said `rm -rf build`, which is the least informative
  true thing that could be shown. Two narrower forms of "preview before you
  approve" were open, and neither is the VM-dry-run idea
  [`ROADMAP.md`](../ROADMAP.md) rejects (that one runs a side-effecting command
  twice; these run nothing):
  - ~~**Compute the affected set, execute nothing.**~~ **BUILT 2026-08-13 (5.6,
    first form).** A card for a command this can confidently read as a delete now
    carries one extra plain line, "About to delete 1,240 files in 12 folders. 3
    of them were changed in the last day." The count comes from a bounded,
    link-free directory walk in the shell (`shell.previewDeletePaths`, capped at
    5,000 entries; over the cap the line says "more than"), because the core has
    no filesystem of its own. Nothing is executed, copied or sandboxed.
    **The classifier fails towards SILENCE** (`agent_core/delete_preview.py`): a
    pipeline, a glob, a variable, a substitution, an unknown flag or an
    unresolvable target produces NO line and a card identical to yesterday's.
    That is the opposite direction from the verb list `run_command` rejected for
    REFUSAL decisions, and deliberately so: this preview is advisory, it adds a
    line to a card that still shows the command and still requires an approval,
    and the failure worth avoiding is a wrong number rather than no number.
    Developer/Custom only, because `run_command` is the only tool it reads and
    that tool is dev-only.
  - **A copy-on-write clone for the file-only subset. STILL OPEN.** APFS `clonefile` is
    instant and free, so the command could run against a clone under the existing
    seatbelt with `network-outbound` denied, and the diff shown. Honest limits:
    it covers only commands that need no network, and it must SAY it skipped the
    preview rather than silently showing none.
  It was **5.6**, not a new step: card and containment work on the step-5 harness,
  the same shape 5.5 was. The clone form, if ever built, is the rest of it.
- **A sandboxed command can reach the network, deliberately.** `network-outbound`
  is granted; `network-bind` is not. Denying outbound was the first draft's
  accidental default and it broke `git fetch` / `npm install` / `pip install`
  while buying nothing: the command's output already travels to a cloud provider,
  so blocking `curl` closes only the useful half. **This makes item 4 (output
  redaction) and untrusted-content screening load-bearing rather than
  theoretical**: they are the only things standing between a prompt-injected
  command and a deliberate exfiltration. The CLAUDE.md deferral for screening was
  written with a trigger ("becomes load-bearing once free/gray-area endpoints and
  MCP tools are in play"); this was a second trigger arriving early, and it needed
  an explicit owner decision rather than silent expiry. **The THIRD trigger went
  live 2026-08-07** when step 7 phase 3 shipped dispatch: a tool server's
  descriptions, schemas and answers now reach a model's context. **The owner took
  that decision on 2026-08-13 and screening is BUILT**
  ([untrusted-screening-plan.md](untrusted-screening-plan.md) owns it): a command's
  output is external content, so it is screened, and instruction-shaped text
  reaches the model with a note in front of it and a kind in the audit row.
  **This gap is REDUCED, not closed.** Screening is a pattern layer over six
  enumerated shapes, an injection written as ordinary prose passes untouched, and
  nothing about it changes what an approved command may reach: the network grant is
  exactly as wide as it was. The other backstops are unchanged and are still the
  ones doing the work (redaction, caps and a card on every single call), stated at
  their real strength in [step-7-mcp-plan.md](step-7-mcp-plan.md) §7. **Phase 4
  re-read it again the same day** against the wider surface it opened, reached the
  same answer, and added ONE thing: a cleaning pass over a server's answer that
  runs BEFORE the redactor, because a credential with a zero-width space in the
  middle of it matches no rule and cleaning afterwards would have handed a model a
  key the redactor had already declined to see. That is a character filter and not
  a screen (it does not read the text and would not notice the plainest injected
  instruction), and §7 says so in those words. **Partly
  mitigated 2026-07-31**: output redaction (`agent_core/redaction.py`) strips the
  credential shapes it knows on the way to the model and the audit trail records
  that it happened, but an unrecognised or deliberately-encoded secret still
  passes, so this stays open and is stated as such in design-doc §9.x.
- ~~**OS-automation directories can be trusted and written today.**~~ **CLOSED
  2026-08-07, the same day it was found, by step 8 phase 1, in the same PR that
  recorded it.** The gap: `workspace_trust_allows` refused only Addison's own
  protected directories, so `~/Library/LaunchAgents` could be granted as a
  trusted workspace and `write_project_file` could put a plist there behind an
  ordinary card: login-time automation, armed, no keyword gate. Closed by the
  fence [step-8-automation-plan.md](step-8-automation-plan.md) §5.5 specifies:
  ONE closed list (`policy.OS_AUTOMATION_DIRS`, hand-synced entry-for-entry with
  `exec.rs`'s copy and pinned by a lockstep test that reads both), THREE
  consumers: the trust floor refuses those directories in both directions at
  grant AND authorize time (so a pre-fence trust row over one stopped confining
  anything the moment this landed, no migration needed), `denylisted_roots`
  refuses a command naming one plus the four arming binaries
  (`launchctl`/`crontab`/`at`/`batch`) as a segment's first word (or behind a
  prefix the shell itself drops: `sudo`, `exec`, `env`…), and the
  seatbelt write-denies them shell-side after every allow, dropping any trusted
  root that touches one. Recorded costs, each stated where the code makes it:
  `~/Library` and `~/.config` are no longer trustable workspaces, and a command
  merely READING a plist is refused by the denylist (which cannot tell read from
  write; the seatbelt, which can, denies only writes).
- ~~**A G3 restore can orphan an armed job, and nothing on any surface can then name
  or stop it.**~~ **CLOSED 2026-08-08 (owner-authorized), by reconcile-on-restore,
  exactly the fix this entry prescribed and none of the ones it forbade.** The gap,
  found by the phase-4 review 2026-08-07: `apply_config_state` is REPLACE-ALL, so
  restoring a snapshot that predates an automation deletes its row, while
  `~/Library/LaunchAgents/<label>.plist` stays installed and launchd goes on running
  it at every login. After that, `disarm_automation` refused ("that automation isn't
  saved any more"), `automation.remove` refused the same way before it could reach
  `_disarm_before_forgetting`, and the Settings section rendered armed-ness per ROW so
  it showed nothing at all. Recovery was `launchctl` by hand. It was the same shape
  phase 3's review fixed for the Remove path (a job nobody can see and nobody can
  stop), reached through Restore instead.
  **What closed it, in three pieces.** (1) DETECTION needed no new question: the
  section already asks `automation.status` (armed LABELS) and `automation.list`
  (rows) when it loads, so an orphan is an armed label matching no row, computed
  where the two answers already meet and filtered to the labels Addison MINTS
  (`com.addison.auto.[a-z0-9][a-z0-9-]{0,39}`) so somebody's unrelated launchd jobs
  are never rendered. (2) THE ROW says *"Running, but not saved here"*, carries the
  label (the only fact left) and says the honest limit out loud: there is no row, so
  Addison cannot show what it runs, only switch it off. (3) STOPPING it is
  `automation.disarmOrphan {label}`, a new RPC that works with NO row, validates the
  label against the set Addison mints before it reads the store or reaches the shell,
  refuses a label that HAS a row (that one has its own controls), and answers in EVERY
  profile: a tightening is never profile-gated, and a Simple person who restored an
  old point is precisely who this strands. G2 is untouched: it can only stop, and the
  structural test now reads the shell bridge's own method set and pins that this
  namespace names `list_armed` and `disarm_automation` and nothing else.
  **The restore itself is unchanged**: never blocked, and nothing silently disarmed
  during one, because an arming decision must not live inside the one action G3
  promises is always available. **The accepted cost, stated where the code makes it
  (`hooks/useAutomations.ts`):** a restore re-reads the ROWS and deliberately does not
  re-ask the OS, so an orphan created while Settings is open appears on the NEXT
  section load rather than at once. Re-asking on restore would be the check nobody
  caused that plan §5.6 forbids, and wrong on its own terms: a restore cannot change
  what launchd holds. Nothing polls.
- **An armed automation may launch Addison itself, and nothing refuses it: an
  OWNER QUESTION, not a defect (raised by the phase-3 review, 2026-08-07).**
  `policy._ARMING_BINARIES` refuses `launchctl`/`crontab`/`at`/`batch` as a
  command's program, but an automation whose command is `open -a Addison` on a
  one-minute interval passes the door, the fence and the keyword card. **My read is
  that this is within G2's letter**: the OS runs it, at the person's explicit
  typed-code consent, exactly like a login item they made themselves, and Addison
  still has no timer, watcher or callback of its own. But it is the one shape that
  produces an Addison-relaunch loop, and the plan's §6 ("no Addison-side scheduler
  … in any phase, ever") never contemplated a job that starts the app rather than
  being started by it. **What it needs is a sentence from the owner**: either "that
  is a person's prerogative" or a denylist entry, rather than a code change made
  quietly on the strength of one reviewer's reading. Recorded here because a
  judgement call that lives only in a review report is one nobody makes.
- **A line inside a heredoc is read as a command, so an ordinary document can be
  refused as "arming" (step 8 phase 1; recorded 2026-08-07).** `_SEGMENT_SPLIT`
  treats every newline as the start of a new command (it must, because
  `ls\ncrontab -` is two commands and that was #48's vector), and the same rule
  reads a heredoc BODY as commands. Since `at` and `batch` are ordinary English
  words, `cat > NOTES.md <<'EOF'` followed by a line beginning *"at last we fixed
  it"* is refused with the arming sentence. Bounded and deliberate: it needs the
  arming word at the START of a line, it is Developer-profile only, and the person
  can run the command in their own terminal. **The cost of the alternative is
  higher**: not splitting on newlines would let `ls\ncrontab -e` past a guard whose
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
  floor from depending on the core's honesty, but a *widened* allowlist is not
  independently checked. Nothing in the tree can widen it today (it is read
  straight from `workspace_trust`); noted so the next thing that touches that path
  knows what it is standing on.
- The data-versus-code edge was **unchanged by this step and was then the sharper of
  the two**: the seatbelt profile denied writes to the data dir but not to a packaged
  `/Applications/Addison.app`. **That has since been closed for a packaged install
  (2026-08-06)**; what is left open is the wording, not the code. It is the same owner
  call opened by steps 4 + 5 and is stated once, below; it is not restated here.

**The keychain integration has a plan (2026-07-31), and its first two steps are
BUILT (2026-08-06):** [docs/secrets-and-keychain-plan.md](secrets-and-keychain-plan.md).
The double-password diagnosis first produced a ground-up encrypted-vault rewrite;
scrutiny (60 findings) and two spikes then **turned it into a repair-first plan**.
Steps 1 and 2 landed on 2026-08-06: presence left the keychain for
`provider_config.secret_presence`, and every credential write is now an explicit,
verified delete-then-add with self-heal on top of it. **Two of step 4's four items
landed the same day**: a definitive 401/403 now marks the provider needs-attention
on a third column, `provider_config.key_rejected_at`, says one plain line ONCE, and
lets routing degrade to another connected provider (§5.2); and a key is normalised
and shape-checked where it is STORED, in `keychain.rs`, rather than trusted to the
frontend's `.trim()` (§5.3). **What is still PROPOSED**: `Intent` and the
background-caller re-arm (§4.3), launch reconciliation (§5.1), the shipped read
counter (§5.6), and the click-anchored cards (§6), which is where a
needs-attention Settings ROW will live; today §5.2's state is core-side plus one
chat-side line. So one item on the old list remains true: G1's zeroization stops at
the Python boundary. The vault survives as a documented destination with named
triggers (step 7's MCP tokens, Android, or the Phase-3 identity rotation). §14 lists
the owner decisions; **decisions 3 and 6 are now answered**; see
[BUILD-LOG.md](BUILD-LOG.md).

**The presence probe cost is CLOSED (built 2026-08-06).** It had been watched
happening on 2026-08-01: with `ADDISON_KEYCHAIN_TRACE=1`, `_primary_key_available()`
(`main.py`) showed up as a real OS keychain read, because **the probe IS the
keychain read**, and it ran on polls with no user action behind it: roughly ten
undismissible dialogs stacked in one session, each orphaned when the app restarted.
Presence is now a SQLite column and no polled or launch-driven path reads a key to
answer it; the `_connections` / `_provider_list` fallbacks and the server's
`_primary_key_available` are gone. What is left is deliberate and person-driven: the
per-turn read (`_primary_key_status`, still fresh, because it is the one caller with
a person behind it), `provider.connect`, and the post-restore keyless note. The one
caller class NOT yet fixed is the background pair the plan's §4.3 owns:
`_maybe_load_live_catalog` and `_maybe_reconnect_saved_providers` still fetch a key
value without a person behind them, which is why `FAILED_READS` survives in
`keychain.rs` as a decline memory (§5.5) rather than being deleted with the poll.

**Self-heal does NOT cover the device-identity item, deliberately (2026-08-06).**
The plan's §4.2 says self-heal "applies to provider keys and the device-identity
item alike". Only the provider keys got it. The reason is the asymmetry §7 of the
plan already names: **a provider key can be pasted again from the vendor's website;
the device identity's private half can be recovered by nobody.** Self-heal is a
delete-then-add, and delete-then-add is the one operation in this subsystem that can
lose data, so running it against the single irreplaceable secret needs its own
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
back to `cdhash H"…"` (a hash of the binary's CONTENTS), and macOS stores THAT
as the ACL entry. Measured on this repo: a correctly-signed build still carried
`designated => cdhash H"1380cf87…"`, so every rebuild presented a new
requirement and the granted permission could never match. That is the original
ad-hoc bug wearing a certificate. Fixed by naming the requirement explicitly
(`identifier "addison" and certificate leaf H"<cert>"`), read from the keychain
rather than hard-coded so it does not silently regress on another clone. Kept
here rather than only in the script because the failure looks exactly like a
user error (pressing the button and having nothing happen), and cost real time
twice.

**Still open from the retired step-1 ledgers:**

- **`tool_grants` capture is still undecided.** Excluded today, and correctly so:
  the table is inert (nothing reads or writes it; `PermissionGate` keeps grants in
  memory). If grants ever persist, restoring a snapshot taken *before* the user
  revoked one would **reinstate** it: a privilege grant delivered by a deliberately
  ungated one-action button. If it is ever captured it must be an **INTERSECT**,
  never a replace.
- ~~**`LiveDatabaseBlocked` should probably be a `BaseException`.**~~ **CLOSED
  2026-08-08** (owner decision), with the verification pass this entry asked for.
  It is a `BaseException` now, so no broad handler can quieten it, and a rebuild it
  refuses answers with the guard's own sentence instead of `_REBUILD_FAILED`,
  which said "your saved restore points wouldn't go back in", a false statement
  about the floor's own storage that also hid the one fixable mistake in play.
  **The audit was small because the surface is**: `Store.__init__` and
  `Store._reconnect` are the only two `sqlite3.connect` calls in the tree, so the
  only broad handlers that can sit between a raise and the top level are the ones
  around a `Store` construction. Two methods now name it, both in `main.py`:
  `_worker_loop`, at the startup build AND per dequeued job (a `BaseException` out
  of a thread's `run()` KILLS it, and a dead worker is the hang §6.5 exists to
  prevent), and `_recover_from_sidecars`, at the rebuild and at the build that
  follows it. `_rebuild_into`'s `except Exception` deliberately no
  longer catches: it was the swallow. `Store`'s own broad handlers all re-raise
  (including an `except BaseException` that already did), and `_reconnect`'s connect
  is on a path the same process already opened, so it cannot newly refuse. **The
  per-job catch is a BELT and is stated as one**: today a build failure
  short-circuits every job two branches earlier, so nothing can reach
  `_ensure_built()` from a job and raise; that is a short-circuit, not a
  guarantee, and the last time this thread was allowed to die every later request
  hung with no frame at all. Its test reaches the branch by putting the server back
  into its pre-build state.
- ~~**`routines/engine.py`: FIVE pre-gate guards each duplicate `on_failure`
  handling.** The unknown-tool refusal, the dev-only guard, the not-callable
  guard, the step-5.5 denylist and the confinement guard each shape their refusal
  as a failed step and re-implement abort / ask_user / skip **inline** instead of
  falling through to the canonical `if not result.success:` block. All five match
  that block today and will silently diverge the moment someone adds a fourth
  `on_failure` policy. **This entry has been overtaken twice**: it was written
  about three guards, phase 2 added a fourth and the 2026-08-07 review added a
  fifth, and each copy was written to match its neighbours rather than introduce a
  new shape. That is the right call for one diff and the wrong equilibrium overall,
  and the rate at which the list grows is now the argument. **Fix by restructuring
  so all six paths share one block**; it is cheaper to do than to keep
  deferring.~~ **CLOSED 2026-08-22**, exactly as prescribed and with no behaviour
  change: the guards were collected into `RoutineEngine._pre_gate_refusal` (which
  returns the first refusal's message, audit outcome and audit detail and decides
  nothing about what happens next) and every way a step can end — the unknown id,
  all five guards, and a tool that ran and failed — now shapes one `ToolResult`
  and goes through `RoutineEngine._record_step`, the single place `on_failure` is
  read. The entry had been overtaken a third time by the step-8 live-only guard,
  so it was seven paths rather than six. `test_routines.py` drives every path
  through every policy (abort / skip / ask_user, both answers) plus the emitted
  step events and the run log, and the parametrization is mutation-proven: a
  policy honoured on the tool-failure path alone fails six ways.

**Open design questions, each blocking a specific step** (moved here from the scope
amendment's §13 when that document was retired, 2026-07-27; the other four §13
questions were resolved during steps 1–3 and went with it):

- ~~**Keyword-gate syntax (blocks step 8).**~~ **ANSWERED 2026-08-07, no longer
  blocks the step.** The syntax is a **per-automation nonce** Addison shows and
  the person retypes (owner decision: a fixed prefix like `!run` is forgeable by
  anything that can write English: observed content can say "type `!run
  install`", but cannot pre-write a code that did not exist yet). The set of
  actions it gates is settled the way the owner's reading pointed: **arming**
  OS-run automation in the harness, never ordinary chat: a one-shot command
  already meets a per-invocation card and the seatbelt, and the recurring,
  unconfined, outlives-the-session nature of an armed job is the jump that earns
  the ceremony. [step-8-automation-plan.md](step-8-automation-plan.md) owns the
  build order and the surrounding decisions. **Phases 1–3 landed 2026-08-07** (the
  fence, authoring, the gate and arming) **and phase 4 (state honesty) on
  2026-08-08**, so this question is answered AND built.
- **MCP tools in SAFE: still open, but it no longer BLOCKS step 7.** Read-only
  only, a curated allowlist, or dev-only? And how MCP tool metadata declares
  undo-ability. **A server declares its own risk, so this cannot be taken on
  trust**; see the sharpened note in the spec's MCP section and item 4 of the
  step-5.5 plan. What unblocked the step was the owner's 2026-08-06 decision that
  MCP is **dev-only for v1**: SAFE admission is deferred rather than answered, and
  no code depends on it: phases 2 and 3 register a server's tools and call them,
  and every one is `open_only`, so the SAFE view has never held one. Promoting a
  tool into SAFE is a later, separate decision.
  [step-7-mcp-plan.md](step-7-mcp-plan.md) owns the step's phases and its other
  decisions. **Transport was the second open question and is now answered: HTTP
  only for v1**, which is why nothing in the step launches a program.
- ~~**Widget capability tiers and vocabulary (blocks step 6).**~~ **CLOSED
  2026-08-06.** The safe interactive kinds are `checklist`, `note` and `timer`, and
  the vocabulary is a **closed, hard-coded set**: a widget spec does NOT declare
  the capabilities it needs, and there is no capability→mode map, because the list
  of kinds is the gate (`agent_core/widgets.py`; [SAFETY.md](SAFETY.md) owns
  invariant 4). Where a widget invokes a tool, the tier check is
  `registry.visible_tools(mode)` and never a second risk model. Code-backed widgets
  are still Developer-only and still unbuilt; when they land they are listed by the
  same `widget.list`, disabled in Simple like every other dev-made artifact.
- ~~**A routine's availability is still decided by its STAMP, not by what it needs.**~~
  **CLOSED 2026-08-08 (owner decision), built exactly as this entry prescribed.**
  Both surfaces (the `unavailable` marker on `routine.list` and
  `_handle_routine_run`'s refusal) now ask **what the routine needs**, through ONE
  function with one owner: `rpc/routines.py::_routine_needs_dev`, true when the plan
  carries a command step (`routine_uses_dev_abilities`) **or** when a step names a
  tool absent from `registry.visible_tools(SAFE)`. A routine of nothing but
  `web_search` steps, saved while Developer was active, is an ordinary Simple row
  again, and it runs. `created_in_mode` still ships as display provenance for the DEV
  badge and decides nothing; `RoutineLibrary.created_in_mode`, the by-id accessor
  that existed only to decide, is deleted, and a source-level test
  (`test_availability_is_never_decided_from_where_a_routine_was_born`) pins that no
  branch in either RPC module names the stamp.
  **The dispatch refusal in SAFE was loosened, which is what made this an owner
  call.** The argument now sits at the refusal itself (`rpc/routines.py`) rather than
  in a document: the engine's per-step `dev_only` check is the real enforcement, and
  a command-free routine replays through `visible_tools(SAFE)` with the gate carding
  per invocation (invariant 3), so nothing widened. Dispatch still refuses a routine
  that NEEDS developer abilities; it stopped refusing one for where it was born.
  **The second half of the entry drove the design.** `routine_uses_dev_abilities`
  alone sees only `step.command`, so a step naming an `open_only` tool
  (`create_automation`, `arm_automation`, `disarm_automation`, `run_command`, every
  `mcp:` tool, and, until 2026-08-11, the two file tools) would have
  slipped through; the question needs the registry as well as the plan, and the
  module boundary rule keeps `routines/` from importing `tools/`, so it lives in the
  RPC layer. The one follow-on line landed in the same commit:
  `rpc/widgets.py::_widget_needs_dev`'s look-through asks that same function, so the
  rail and the library still cannot disagree about one routine, now about the right
  answer. [SAFETY.md](SAFETY.md) owns the rule both halves implement.
- **Auto-routing depth: v2 or now? (half-resolved.)** The AVAILABILITY half
  shipped in step 3: escalate/degrade on unavailable, rate-limit or network
  failure, with per-provider cooldown, a per-**attempt** deadline and the plain
  "X was busy, so Addison used Y" note. The CONFIDENCE half, quality-based
  escalation, remains v2 substrate, untouched.
- **The answering model must be disclosed beside the picker (owner directive,
  2026-08-21).** Evidence from the QA re-run pass: with Claude Haiku 4.5
  explicitly picked and strategy Cost first, an offline turn's Technical
  details showed the router attempting `provider: google · gemma-4-31b-it` —
  the explicit pick does not win, and no surface says which model actually
  answers a given turn. The owner's words: the picker system reads as broken
  when this happens; the answering model should be shown on a tab/indicator
  next to the model picker. This is the cost-first-vs-explicit-pick question
  (KNOWN-BUGS.md, open questions) grown an answer for its UI half; the
  precedence rule itself is still the owner's to settle.
  **THE UI HALF IS BUILT (2026-08-22)** and this entry stays open for the other
  half. The composer's controls strip now carries one quiet line left of the
  picker — `Answered by <label>`, mono 10.5px `disabled`, no accent (the accent
  is for actions, selection and live state, never a disclosure) — drawn by
  `shell/src/components/Composer.tsx`. It is **derived from the thread**, never
  stashed: the newest assistant message carrying `answeredWith` (contract D5).
  That is deliberate — the fact rides on a send REPLY, so it exists only for
  turns answered in this session, and `useConversations` replaces the messages
  array wholesale on open/new-chat, so opening another conversation takes the
  line with it rather than leaving a previous chat's model on screen. Nothing in
  routing, the router or any precedence logic was touched, and the line says only
  what answered — it does not editorialise about what SHOULD have. Pinned by nine
  tests in `shell/src/__tests__/composer.test.tsx`; the transcript's separate
  free-model chip is unchanged. Same pass, same
  register: the "Tools" page reads as "what Addison can reach" (providers,
  folders, servers) while its name promises the tool registry — recorded as a
  naming observation, not scheduled.

**Moved here from `VERIFICATION.md` §4/§6 (2026-07-26)**: that file had become a
second live-issue register holding items this one did not have. All checked
against the tree on 2026-07-26:

- ~~`RoutineLibrary` shares one `values` map across routines.~~ **CLOSED
  2026-08-01.** `values` is now scoped by a `valuesFor` routine id and only sent
  to the routine it was entered for. **The repro in this entry was wrong and the
  fix is narrower than it looked:** `executeRun` clears `values` in its `finally`,
  so *completing* routine A cleans up after itself. The reachable path is
  **abandoning** a fill: open A's fill panel, type an answer, then run B (which
  needs no input, so it skips the fill step and runs immediately) and B carries
  A's answer under the shared name. Mutation-proven; a first version of the test
  passed under mutation because it ran A to completion first.
- ~~**Empty-text `sendMessage` has no guard.**~~ **CLOSED 2026-08-08** (owner
  decision). `_run_send_message` refuses empty or whitespace-only text (and a
  non-string `text`, which would otherwise have persisted `str(None)` as somebody's
  message) before anything is read, cleared or written. It is **the core keeping
  its own invariant, not a second copy of the composer's rule**: `Composer.tsx`
  already trims and disables the button, and the CLI skips a blank line, so no
  shipped caller reaches it. That is exactly why it was missing, and a guard whose
  only proof is "nothing calls it wrongly" is not a guard. Placed ahead of the
  pending-pick reset as well, so a refusal cannot silently spend a
  `setRoleForNextMessage` the person made for the message they are about to write.
  *(The guard immediately found four tests sending `{"content": ...}` where the
  wire field is `text`; they had been running whole turns on an empty user
  message, which is the litter this entry describes.)*
- ~~**Local-setup pre-flight HTTP runs on the read loop.**
  `_handle_start_local_setup` (`agent_core/main.py`) is an inline dispatch handler
  and calls `is_running()`, which can block frame delivery up to 5s.
  `availableRoles` was moved off the read loop for exactly this reason; same shape
  as `shell.pickDirectory` blocking the worker on a modal.~~ **CLOSED 2026-08-22.**
  `model.startLocalSetup` is a worker job (`start_local_setup`) now, moved by the
  same one-line change `availableRoles` took, so the five-second Ollama probe can
  no longer stop the read loop from delivering the `permission.respond` or
  `conversation.stop` that would end the turn underneath it. It queues behind an
  in-flight turn, which is the accepted half of that trade.
- ~~**Three stale-docstring flags, still UNVERIFIED.**~~ **All three resolved
  2026-08-06: one was real, two were the stale thing.** `openai_provider.py` was
  REAL and is fixed: its module docstring said the custom base URL is "validated
  http(s):// at connect time (main.py)", and that validation is
  `rpc/providers.py::_valid_http_url`; the RPC split moved it and the reference
  did not follow. `ModelRouter.register` (`providers/router.py`) is **accurate**:
  it names `DirectAPIProvider`, which exists (`providers/direct_api_provider.py`),
  and `register` really is additive per role. The `PermissionRequest` dataclass
  (`permissions/gate.py`) **has no docstring at all**, so there was never anything
  there to be stale. Both flags deleted rather than re-verified again: a flag that
  survives two checks against a thing that does not exist is itself the defect. The
  fourth,
  **`default_cloud_model([])`, was real and is CLOSED 2026-08-01**: its docstring
  called `catalog[0]` "a safe fallback" while an empty catalog raised
  `IndexError`. It now raises `ValueError` naming the cause. No caller can reach
  it today (all three guard first), so this is for the next one: an empty live
  catalog fetch should say what went wrong, not surface three frames away.
- **Polish, unstarted:** no conversation search in the sidebar; **scoped consent
  ("always allow" per site)**: a SAFE grant is keyed by tool id, so after the
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
  `_audit`'s best-effort `except` on an upgraded one: a log that quietly stops
  logging, which is worse than no row at all. `Store._migrate_tool_audit_outcomes`
  rebuilds the table, preserving every existing row, and the
  vocabulary gained `not_callable` (the refusal this entry was about) and `failed`
  (the gate said yes and the call never landed). Both dispatch paths write both.
  *(The rebuild's first version preserved those rows only when nothing interrupted
  it; how that was closed is in [BUILD-LOG.md](BUILD-LOG.md).)*
  The refusal branch itself is now quiet for MCP tools (they are callable) and
  remains the mechanism `mcp_catalog.MCP_TOOLS_ARE_CALLABLE` operates through.

**Opened by step 7 phase 4 (2026-08-07):**

- **A tool server that answers in pictures is a tool server Addison cannot use.**
  Phase 4 counts and discloses `image` / `audio` / binary-resource parts and
  forwards none of them ([step-7-mcp-plan.md](step-7-mcp-plan.md) §4.4, decision 1),
  so a server whose whole output is a chart returns *"nothing Addison can pass on"*
  plus a count. That is the deliberate answer and it is the right one for v1.
  Provenance, not capability, is the objection: the machinery to carry an image to a
  model exists (`read_file` → `_gate_image_result`) and it carries a file **the
  person picked**, not bytes a program nobody has audited pushed in unasked.
  Recorded here rather than only in the plan because it is a real limitation
  somebody will meet, and because the upgrade path is specific rather than
  hypothetical: route a server's image through the same vision gate, behind the
  same per-invocation card, once there is a reason to trust the provenance,
  which is the promoted-allowlist decision wearing a different hat, and is
  therefore the same later conversation.

**Opened by the 2026-08-07 review of all four step-7 phases:**

Two shapes of credential still cross `agent_core/redaction.py` untouched, and both
are deliberate as far as they go. The redactor is a **backstop, not a boundary**
(its own header says so and [step-7-mcp-plan.md](step-7-mcp-plan.md) §7 owns the
strength that may be claimed for it), so these are not bugs against a promise. They
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
  **What would close it** is not a wider character class. Screening was the answer
  written down here, and screening was built on 2026-08-13
  ([untrusted-screening-plan.md](untrusted-screening-plan.md)) **without closing
  this gap**, which is worth stating plainly rather than leaving a reader to infer:
  screening looks for writing shaped like an instruction to an assistant, and a
  split credential is not shaped like one. It reduces the surrounding exposure by
  marking the passage a split key may have arrived in; the key itself still passes
  the redactor. **STILL OPEN**, and it needs a control that reads credentials.
- **A fullwidth or homoglyph credential (`ＡＫＩＡ…`) is not caught, and NFKC
  normalization was deliberately not half-built.** Folding a copy of the text and
  matching against the fold finds the key and then cannot say where it was: the
  folded string has different offsets from the original, so replacing what was found
  means the redactor must expose SPANS and map them back: a change to the shape of
  the most safety-critical file in the tree, made for a shape nobody has yet been
  seen to send. The half-built version is the one that must not exist: a redactor
  that matches on the fold and returns the original names a kind in the audit row it
  did not actually remove from the text, which is worse than this gap, because this
  gap at least reports itself honestly. Owner call, with the cost written down.

**Opened by steps 4 + 5: decide these, don't rediscover them:**

- **The webview cannot open an external link, at all.** `main.rs` registers four
  commands for it (`send_to_core`, `store_provider_key`, `delete_provider_key`,
  `restore_replaced_provider_key`), and not one of them opens anything;
  `shell.openExternal` is CORE→shell, reachable only by the `open_link` tool, and
  `Markdown.tsx` states the rule as "the webview must never open URLs itself, and
  must never call any `shell.*` IPC method". So every address shown in Settings is
  copy-paste text (the Google free-tier line now says so honestly), and
  `Markdown.tsx`'s inert anchors are inert for the same reason. If clickable links
  are wanted, the fix is **one narrow webview→shell Tauri command**, not an anchor,
  and it is new highest-trust surface, so it is an owner call, not a cleanup.
- **The Custom guard panel still has no workspace-trust guard**, which CLAUDE.md
  and this file both said step 5 would add ("as those capabilities land, never
  before"). It was not in the frozen step-5 contract, so it was not built. In the
  meantime the precedence question is answered defensively: `auto_grant_scope='none'`
  now beats trust (see rigor-pass item 6). **This is an open owner call with no
  step left to carry it**: does the panel grow a third guard, or is that precedence
  rule the whole answer? It was scheduled against step 6 and then step 8; 6 shipped
  2026-08-06 without touching it (it turned out to be entirely widget-side) and 8
  completed 2026-08-08 without touching it either, and `GuardConfig` in
  `agent_core/policy.py` still has exactly two fields. **Do not re-schedule it
  against the next step**: the last two deadlines passed silently, which is what a
  deadline nobody owns does. Decide it, or record that the precedence rule is the
  answer and close this entry.
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
  confinement. **Still open — but DOCUMENTED AT THE FUNCTION since 2026-08-22**, so
  it is a known platform note rather than a silent assumption: `_canonical`'s
  docstring now states that the fold is a macOS default-volume assumption, which
  direction each caller errs in when it is wrong (the protected-dir refusal toward
  refusing, workspace confinement toward admitting a sibling of a trusted folder),
  and that a fix would have to decide per-VOLUME rather than per-platform, since
  both kinds mount on one Mac. **The behaviour is unchanged.**
- ~~The floor protects Addison's DATA, not Addison's CODE.~~ **CLOSED FOR A
  PACKAGED INSTALL, 2026-08-06.** *(This is the single statement of it; SAFETY.md,
  design-doc §9.x and HANDOFF all point here.)* In a packaged install the model
  could rewrite `policy.py` inside `/Applications/Addison.app` card-free, which is
  a more complete bypass than deleting the snapshots ever was. The running app's
  BUNDLE now joins the protected set (`filesystem.rs::addison_app_bundle`), so the
  seatbelt denies writes to it exactly as it denies the data dirs: one mechanism,
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
- **A hardlink inside a trusted root to a file outside it is trusted**: `realpath`
  cannot see hardlinks. Inherent to any realpath-based confinement; noted rather
  than fixed.
- ~~**Two spellings of a file that is NO LONGER THERE keep one revert chain each.**~~
  **CLOSED 2026-08-08 for every row written from now on, and the claim this entry made
  while it was open was wrong.** It said a wrong SPLIT "only leaves two rows where one
  would do, and each still reverts to a state that actually existed on disk". It does
  not. On the case-insensitive volume macOS ships with, `Notes.md` and `notes.md` are one
  file and one chain; delete the file and the chain split into two rows, one of which
  offered `before='v1'` (a state ADDISON wrote) under "the way it was before Addison
  changed it", and reverting the v0 row recreated the file so the other row was no longer
  pointing at nothing: the next "Undo last action" wrote v1 back. That is the S1/S2
  resurrection `file_revert`'s chain collapse exists to make impossible, reached from the
  other end.
  **What closed it:** the grouping no longer asks the disk at read time. Every write
  records the file it landed on (`wrote_ident`, minted by `revert_key`), and a chain is
  the rows joined by the same recorded NAME or the same recorded FILE: facts about the
  past, which deleting a file cannot change.
  **What is still open, and it is narrower:** a row written BEFORE 2026-08-08 carries no
  identity, so it falls back to asking at read time and both hazards remain for it: the
  split above, and the hard-link merge below. Rows are never migrated (the payload is TEXT
  holding JSON and a migration would be inventing a fact about the past), so this ages out
  as those rows are reverted or retained away rather than being fixed.
- **The file identity behind all of this is an inode number, and inode numbers are
  REUSED on ext4, just not on what Addison ships (found by CI, 2026-08-08).**
  `file_revert.revert_key` answers `file:{st_dev}:{st_ino}`, and the whole
  hard-link/case-collision repair rests on two names being the same file iff that pair
  matches. On APFS the assumption holds outright: inode numbers come from a monotonic
  counter, and a hunt measured 4000 create/delete cycles with 4000 distinct numbers and
  no reuse. On ext4 the freed number is handed straight back, so a file deleted and a
  different one created can carry the identity Addison recorded for the first, which
  would let `another_file_stands_there` accept a file it never wrote, and could join two
  unrelated chains. **Not reachable on the shipping platform** (macOS only: launchd, the
  seatbelt, a Tauri macOS build), and the join is `name OR identity`, so a wrong identity
  match still needs the row's own name to be involved. It surfaced because the Linux CI
  runner made a test's own fixture stop proving anything; the test now renames a
  replacement over the name instead, which allocates the new inode while the old file is
  alive and cannot collide anywhere. Recorded rather than fixed: the fix is a
  cheaper-than-it-sounds `st_ctime`/`st_size` tiebreak, and it belongs with any decision
  to support a second platform, not before it.
- **A revert refuses where the file at that name was REPLACED since Addison last wrote
  it** (opened 2026-08-08, deliberately: the cost of closing the two above).
  `file_revert.another_file_stands_there` lets a chain go back onto a file it actually
  wrote, or onto a name with nothing at it, and refuses anything else, which is what
  stops a hard link planted at a written path from taking one file's prior bytes into
  another. The same rule catches a case nobody planted: `git checkout` and editors that
  save by rename put a NEW file at the name, so a chain whose newest write predates such a
  swap is refused rather than overwritten (*"A different file is at that name now…"*).
  A swap BETWEEN two of Addison's own writes is not affected: those rows are one chain
  and it goes back onto the newer file. The BEFORE pane still holds the text to copy,
  which is the answer this module already gives for a partial revert; the alternative was
  to keep overwriting whatever now stands there, which is the harm. **If this proves
  annoying in practice**, the honest widening is a confirm that names the file, never a
  silent overwrite.
- ~~**A refused undo says less than it knows.** `WriteProjectFileTool.undo()` raises a plain
  sentence for the case above; `rpc/undo.py::_undo_last_action` replaces every failure with
  *"Couldn't undo the last action. You may need to reverse it yourself."*, as it already
  did for the no-shell refusal. The review surface's Revert shows the real sentence. Redo
  already surfaces `result.detail`; undo cannot simply copy that, because an undo failure
  can also be a bug's exception text and no stack trace may reach a person (CLAUDE.md).
  The fix is a refusal that is typed rather than stringly (a flag on `UndoResult`), which
  is a change to a shared mechanism and is written down here rather than made in passing.~~
  **CLOSED 2026-08-22**, typed exactly as prescribed: a tool that means to speak raises
  `tools/base.py::UndoRefused`, `UndoManager` flags that one arm as `UndoResult.refusal`,
  and `_undo_last_action` shows a flagged detail verbatim while every unflagged failure
  keeps the generic sentence — so the default stays safe and a new failure mode is generic
  until somebody writes its words. Seven refusals across five `undo()` implementations moved
  over (`write_project_file` ×2, `save_file`, `draft_message`, `arm_automation` ×2,
  `create_automation`); Redo is untouched, and the response shape did not change, so the
  frontend renders the sentence through the `detail` it already read.
- ~~**The shell follows a shortcut planted at a path it once wrote** (2026-08-08).~~
  **CLOSED 2026-08-22, the shell's own half last.**
  `restore_workspace_path` checked its session ledger against the NAME and then
  `fs::write`s that name; `read_workspace_view` opened it. Neither asked whether a
  symlink now stood there, so a path Addison legitimately wrote was a write-through to
  wherever that name later points, and it took no attacker to arrive, only somebody
  moving a config file into a dotfiles folder and linking it back. The review surface
  refuses first, core-side: `file_revert.replaced_by_a_link` guards the diff's read and
  the revert's write, and what crosses is the RECORDED path rather than a re-resolution
  of it. **The chat header's Undo stopped writing through such a link on 2026-08-08**:
  `WriteProjectFileTool.undo()` now asks `another_file_stands_there` first, and a shortcut
  standing at a written path reaches a different file by that question too, so nothing is
  written (it used to put its prior bytes into whatever the link pointed at: a private
  key, in the test that plants one).
  **What closed the SHELL's half, 2026-08-22:** `filesystem.rs::refuse_shortcut_at_path`
  asks `symlink_metadata` — never `metadata` — of the recorded name itself, and both
  methods take it before anything is opened or written (the viewer ahead of its own stat,
  which follows the link on purpose because it measures what would be loaded; the undo
  ahead of both its branches, the delete included). Nothing follows the link and nothing
  acts on its target. So the two cases no core guard reaches are refused anyway: a future
  caller that has not asked core-side, and a row written before `wrote_ident` existed,
  which cannot ask. It is only the name ITSELF — a link in a parent directory stays
  `refuse_addison_data_dir`'s chain walk, a different question with a different owner.
  **What it costs, stated:** `write_workspace_path` follows a link, so the LINK can be the
  ledgered name (a config file linked back from a dotfiles folder), and that undo is now
  refused rather than written through — which is already the answer both core guards give.
  **Cosmetic consequence of the same swap, not a second gap:** a row whose
  recorded path is now a shortcut lists with `root: null` and its whole path, because
  the display comparator (`policy.path_is_within`) resolves both sides. `root` permits
  nothing; it decides only what the row renders as.
- **The shell's file floor does not know the OS automation directories, and
  `exec.rs`'s does** (found by the 2026-08-08 adversarial pass; recorded, not
  closed). `filesystem.rs::refuse_addison_data_dir` guards every workspace read and
  write against Addison's own data dirs and bundle. The step-8 fence
  (`exec.rs::OS_AUTOMATION_DIRS`) guards a different set (`~/Library/LaunchAgents`
  and the ten other places where writing a file IS arming a job), and only in the
  seatbelt profile around `run_command`. So `write_project_file` naming a plist path
  is refused by the CORE (twice: `policy.workspace_trust_allows` on the grant and
  the pre-gate denylist on the call) and by nothing in the shell. Both core
  refusals are mode-independent, which is what kept this unchanged when the file
  tools reached SAFE on 2026-08-11: a Simple-profile edit meets exactly the same
  two checks.
  **Left open deliberately, with the cost stated.** Closing it means ungating
  `OS_AUTOMATION_DIRS` from `#[cfg(target_os = "macos")]` and giving a hand-synced
  three-consumer list a fourth consumer in a second module, while the fence's own
  test pins the count precisely because that list drifts. The floor
  `refuse_addison_data_dir` states is "Addison's own memory", and automation dirs are
  a different floor (G2) with a different owner; folding them in would make one
  refusal sentence answer for two unrelated properties. What it would BUY is defence
  in depth for a path the core already refuses in two places, which is worth having,
  and is the reason this is written down rather than dismissed. **Owner's call.**
- ~~**The name on the card is resolved a SECOND time, so it can go stale between the
  label and the effect.**~~ **CLOSED 2026-08-08**, in the review surface's read-paths
  work as this entry scheduled it ([`phase-3-review-surface-plan.md`](phase-3-review-surface-plan.md)
  Build §1). The label and the boundary now share ONE resolution: the caller resolves
  above its refusal branches and passes that value to `call_permission_detail`, which
  hands it to the tool's new `permission_detail_for_path(resolved_path)`. A path tool
  no longer sees `args` at that seam at all, so there is nothing left to resolve a
  second time even if a later edit wanted to, which is why it is a second HOOK rather
  than a second parameter on the first. Two things the entry did not foresee. The
  resolution had to move UP in both dispatch loops, above the denylist and arming
  branches, because the audit rows written there name a file too, and they were
  re-resolving as well, so the fix is one resolution per CALL, not merely one shared
  between the card and confinement. And the proof is not a symlink swapped mid-race
  (that is a race, and a test of one is a flake): a fake tool whose `affected_path`
  answers a DIFFERENT path the second time it is asked fails on ANY second resolution,
  which is stricter than the thing it stands in for. The live loop, the routine engine
  and the refused-before-the-gate branch each have their own test; the widget rail
  passes nothing and says why at the code (its only tool has no `affected_path`).
- **`revertable` is ONE boolean carrying THREE different facts, and the surface can
  only render the vaguest of them.** `_edit_payload` sends
  `"revertable": bool(restorable)` (`agent_core/rpc/workspace.py`), and
  `_restorable_map` returns `{}` (false for every listed edit) in three unrelated
  situations: there is no shell bridge, the single batch
  `shell.canRestoreWorkspaceFiles` call raised, or the shell genuinely does not hold
  that path in its session write ledger. Only the third is the restart case. The
  review surface's line asserted it for all three, so ONE failed batch call printed
  *"Addison changed this before the app was last restarted, so it can't put it back
  for you"* under every row on screen, including a file Addison had written a minute
  earlier. **Mitigated frontend-only on 2026-08-08**: `NOT_REVERTABLE_LINE`
  (`shell/src/components/CodeSurface.tsx`) now names no cause at all: it says only
  what is true in all three cases, that Addison cannot put the file back and the
  earlier version is on the left. That is honest and less useful, and it is where it
  stays until the core can tell the three apart.
  **The wire shape that would let the sentence come back**: make the field TRI-STATE
  exactly as `onDiskChanged` already is on the same payload: `true` / `false` /
  `null`, with `null` meaning "Addison could not find out" (no bridge, or the query
  failed) and `false` reserved for the shell's real "not in my ledger". Then the
  surface renders three sentences for three states, the way it already does for
  `onDiskChanged === null` (*"Addison can't tell whether this file changed since."*).
  It touches `_edit_payload`, `agent_core/protocol.py` and its hand-synced twin
  `shell/src/types/protocol.ts` (`WorkspaceEdit.revertable: boolean` →
  `boolean | null`), so it is a core + protocol change and was deliberately not made
  from the frontend side.
- ~~**`workspace.pickDirectory` blocks the worker thread** on a modal dialog with the
  bridge's 60s ceiling; browse for longer and the timeout is swallowed into
  `{"directory": null}` with no explanation, while every other store RPC queues
  behind the open dialog.~~ **CLOSED 2026-08-22**, both halves. It is no longer a
  worker job at all: the handler starts one short-lived thread and answers from
  there (`main.py::_handle_workspace_pick_directory`), which it is allowed to do
  because the RPC is store-free — the `_ensure_built()` it used to call was the
  only thing tying a folder dialog to the SQLite queue. And a timeout is no longer
  a cancellation: `IpcShellBridge._call` raises `ShellCallTimeout`
  (a `RuntimeError` subclass, so every existing catch is unchanged) and the handler
  answers `{"directory": null, "error": "Addison stopped waiting for the folder
  picker…"}`, which `useWorkspace` puts on the panel's existing error line.
  **The 60s ceiling itself is unchanged** — this makes it audible, it does not
  raise it; if browsing past a minute turns out to be ordinary, the human-paced
  precedent to copy is `_KEYCHAIN_TIMEOUT`.
- **The CSP blocks Tauri's own custom-protocol IPC, and whether to admit it is an
  OWNER DECISION** (found 2026-08-08, verified against tauri 2.11.5, the version in
  `Cargo.lock`). `connect-src 'self'` does not admit `ipc:` (macOS/Linux) or
  `http://ipc.localhost` (Windows), and **Tauri does not inject them**:
  `tauri::manager::set_csp` augments `script-src` and `style-src` with nonces and
  hashes and touches nothing else; Tauri's own documentation has the app author
  `connect-src ipc: http://ipc.localhost` by hand. So `scripts/ipc-protocol.js`'s
  `fetch(convertFileSrc(cmd, 'ipc'))` is refused, Tauri catches it and falls back to
  `window.ipc.postMessage`, and every invoke since has gone that way.
  **This is not a regression and nothing is broken.** The policy that shipped before
  was `default-src 'self'`, which blocked the same fetch identically; the app has
  only ever run on the postMessage path.
  **What changed is that it is now AUDIBLE.** `installCspViolationReporter` (shipped
  2026-08-08) pushes a diagnostic for every violation, so the app's own IPC produces
  one on each launch: exactly the recurring noise that would train a reader to
  ignore the pane and mask a real Monaco or worker violation.
  **Taken: the narrow half.** The policy is unchanged and the REPORTER is taught to
  pass over that one endpoint, by name and with the reason written at the code. The
  violation is still real, still enforced, and still visible in devtools; what is
  suppressed is a diagnostic about a fallback the app was designed around.
  **AMENDED 2026-08-08: the macOS/Linux half was never narrow, and is now gone.**
  CSP3 §5.4 strips a blocked URL for reporting and returns **the scheme alone** for a
  non-http(s) URL, so `ipc://localhost/plugin:…` reaches a compliant engine's report
  as the three characters `ipc`: no host, no path, nothing to name. The reporter's
  `blockedURI === "ipc"` branch was therefore a filter on a SCHEME wearing a named
  allowance's docstring: it dropped every `ipc:` `connect-src` violation there could
  be, including page script calling `fetch("ipc://localhost/plugin:fs|remove")`, a
  direct command invocation, and the one violation on that scheme that would mean
  something. The two are byte-identical in a report, so they cannot be told apart;
  the branch is removed and the scheme-only shape is REPORTED. **The accepted cost is
  one benign diagnostic per launch on macOS and Linux**, which is the honest price of
  never hiding the other one. What still suppresses is the host-bearing list
  (`http(s)://ipc.localhost`, which is what Windows actually files, stripped to its
  origin), narrow not because it reads intent, which no report allows, but because
  it can only ever hide a violation naming that one endpoint.
  **NOT taken, and this is the owner's call:** adding `ipc:` and
  `http://ipc.localhost` to `connect-src`. It would let the custom-protocol IPC path
  run for the first time in this app's life (a behaviour change nobody asked for, on
  the highest-traffic seam there is), and it widens the one directive that governs
  where a local-first app may talk to. It would also need a named exception in
  `tests/test_csp_is_pinned.py`, whose vocabulary rule refuses `ipc:` and every
  `http://…` on purpose. Worth doing only if the postMessage path is ever measured to
  be the problem; the test refuses it today so that the decision has to be made out
  loud rather than to quieten a warning.
- ~~**A failed endpoint add still clobbers the keychain**~~, **CLOSED 2026-08-08**
  (owner decision) **with the rollback, not just the disclosure.** The ordering is
  unchanged and unchangeable: the key is saved before the connect because the core
  reads it from the OS at connect time and it may never be a parameter of a core
  frame (G1). What changed is that the save is now UNDOABLE. `keychain.rs` records
  what each overwriting save replaced, and a fourth Tauri command
  (`restore_replaced_provider_key`) puts it back: the previous key rewritten
  through the ordinary delete-then-add-and-read-back ladder, or the item removed
  where nothing was saved before.
  **Why shell-side and not core-side.** The save happens in the shell at the
  webview's request, so its undo belongs at the same seam; the core learns nothing
  new, gains no new power, and is not involved at all. The webview sends a provider
  id and gets back one boolean; no key value crosses in either direction, so G1 is
  untouched by the new command. It costs no extra OS touch either: the previous
  value comes from the read the save already performs (`save_verdict`), because a
  second look would be a second password dialog.
  **What it deliberately will NOT do, and what remains.** It never guesses. A
  rollback runs only where the previous state was positively KNOWN: a value read,
  or a definite "nothing saved". A read that merely FAILED (a dismissed password
  dialog) records nothing, the rollback answers "couldn't", and **the caller then
  discloses**: both call sites (the add-a-server card and the Settings connect row,
  which had the identical clobber for all four providers) append one plain sentence
  saying the new key replaced the old one and naming the fix. So the disclosure half
  survives as the floor for the case that cannot be undone. Also left standing,
  deliberately: after a rolled-back failure the stored `base_url` is the NEW
  server's while the key is the old one, and `secret_presence` may read `present`
  when the rollback removed the item: the first is what G3's `add_endpoint`
  restore point is for, and a stale `present` is the safe direction by design
  (`Store.record_secret_presence`: it can never reach the relay).

- `draft_message` compose handoff: Rust returns "not available yet"; a real
  discardable-draft mechanism is required by the undo invariant.
- No file-attach/drop UI → `read_file` unreachable from chat.
- Setup Assistant relay is client-complete; the server side is external by design.
- Packaging/signing/updater = Phase 3.
- ~~**`primary.txt` widget guidance says Addison can't build custom-app widgets.**~~
  **Rewritten 2026-08-06 with step 6 half A**, which is what it was waiting for: the
  guidance now names the checklist, note and timer as things Addison really makes,
  states the two limits worth hearing early (a checklist's lines are fixed at
  creation; a timer never rings, because nothing runs by itself), and keeps the
  refusal for what is still not a widget: a calculator, a game, a watcher. The
  never-save-a-file-instead rule is unchanged and still load-bearing. It remains
  MITIGATION, not a mechanism: it failed once (#43) and was re-hardened once (#45),
  and a third regression should go structural: a registry-level guard on
  `save_file` calls that look like widget substitutes.
- **The design-doc and engineering-spec *bodies* predate the SAFE/OPEN
  mode-scoped model and have no widgets section.** They carry amendment banners
  and precedence notes, but a dedicated reconciliation pass would be worthwhile.

**Opened by the Context Budget Manager (built 2026-08-14;
[context-budget-plan.md](context-budget-plan.md) owns the subject and states its
honest limits — two of the three below are now closed):**

- ~~**The boundary marker is ephemeral, so spec §4.8 item 4 is only partly
  served.**~~ **CLOSED 2026-08-22**: `conversation.load` now sends the stored
  `continued_from_conversation_id` and `summary`, and the thread draws a marker
  from them above the first message — the same fact the per-turn note says once,
  still on screen on the tenth reopening, with the summary behind a disclosure.
- **A continuation's summary call is invisible in every cost view.** The call is
  made at the RPC layer, which has no resolved provider id or model id to attribute
  a row to, and `usage_log` rows are written by the orchestrator's `on_usage` at its
  choke point with both identities in hand. A row attributed to the wrong model is
  worse than a missing row, because `tokens_month` and the per-provider latency stat
  are exactly the numbers somebody uses to decide what to run. So the deliberate
  answer for now is a missing row, and the cost of it is bounded rather than
  unbounded: at most one call per turn, with at most 60,000 characters of input. It
  still understates what a long chat costs. Closing it means carrying the resolved
  identity out to the boundary, which is a change to the callback shape and not a
  patch.
- ~~**A continued chat is two rows in the history sidebar.**~~ **CLOSED
  2026-08-22**: `conversation.list` rows carry `continuedFrom`, and the sidebar
  folds a lineage into one entry — the newest part keeps the row, each older part
  sits under it indented and marked `earlier`, and the group counts the chat once.
  Nothing is hidden: both conversations are still drawn and still open, which is
  what keeps the untouched original transcript reachable.

**Opened by routine sharing (built 2026-08-15;
[routine-sharing-plan.md](routine-sharing-plan.md) owns the subject and states all
four of these as what remains uncaught):**

These are attacks a shared routine can carry that nothing in the feature catches.
They are tracked rather than closed because each has a real answer that is bigger
than the feature was, and because the honest sentence about all four is the same:
the plan carries zero permissions and every action in it still takes its card, so
what is missing is the WARNING, never the gate.

- **Injection phrased as ordinary prose is not flagged at import.** Screening is six
  enumerated shapes, and a description written as plain, reasonable text that a model
  will nonetheless act on reaches the model unmarked. Same standing limit as every
  other screening origin ([untrusted-screening-plan.md](untrusted-screening-plan.md)
  owns it); listed here because a routine description is read by a model on every
  later run, which is a longer-lived exposure than one web page in one turn.
- **The taint card is one edge, and three shapes of the same attack sit outside
  it.** Its trigger is exact containment of a file-read output inside a
  network-bound step's resolved arguments within one run. Text that a middle step
  reworded, summarised or translated is a new string and is not found; a chain
  across two routines is two runs and taint dies with the run; and contents a person
  pasted into a variable by hand never passed through a file-reading step at all. In
  every case the ordinary card still appears and only the flow sentence is missing.
  Widening it means general taint tracking, which owner decision 4B declined for a
  stated reason: a card line that guesses wrong is a card people learn to click
  through.
- **A plan whose danger is entirely in substituted values looks unremarkable at
  import.** The preview can show the steps and the questions, and cannot show
  resolved arguments, because they do not exist until the routine runs and the
  person answers. The run card is where those values appear, per invocation, and is
  the control this rests on. Anything that tried to close it at import would be
  guessing at values nobody has entered.
- **`tool_grants` capture is still open, and sharing makes it sharper.** It is the
  last step-1 deferral (listed above) and would have to be an INTERSECT. A restore
  that put grants back is one thing on a machine where every grant was made by the
  person; it is another beside a library that can now contain a plan somebody else
  wrote. Nothing has changed about the deferral itself, and this is the argument for
  which side it must fail towards when it is built.

**Feature suggestions judged 2026-08-09 (owner-reviewed), recorded in their
judged shapes so the raw suggestions are not re-litigated later:**

An outside list of features was propped against what exists. Two were already
covered or contradicted by recorded decisions and are NOT open: copy-and-regenerate
is design-doc §7.9.1's Retry, and a web-search / code-execution on-off pair exists
in a stronger shape than toggles: consent cards for search, and code execution is
a profile rather than a switch (`run_command` is absent from `visible_tools(SAFE)`;
a Simple-profile toggle for it would break SAFE invariant 1). The rest survive, as
follows. None is scheduled and none blocks anything.

- ~~**Highlight → Ask / Explain: the one worth building first.**~~ **BUILT
  2026-08-22, in the judged shape.** Selecting text in a
  past message offers Ask (type a question about the selection) and Explain (the
  same mechanism with a canned prompt: one feature, two labels). The judged shape:
  a selection popover (floating chrome, the sanctioned bordered-panel element in
  the v4 direction) that inserts the selection as a QUOTE plus the question into
  the main thread. Deliberately NOT the suggested "small window with the AI
  responding in it": a second parallel chat surface forks the single-thread
  correspondence UI and mints new state to manage, restore and explain. Read-only,
  frontend-only, no new tool, no gate or registry work, works in every profile,
  and the best persona fit on the list, because an unclear sentence is exactly what
  a non-technical person cannot phrase a follow-up about.
  `shell/src/components/SelectionAsk.tsx` is what shipped: both buttons take the
  SAME path — App's one-shot `composerSeed`, the mechanism the empty-state chips
  already used — so what lands is a markdown blockquote of the selection, a blank
  line, and (Explain only) "What does this mean, in plain language?". Nothing
  sends: the person reads, edits and presses Send. It is offered only for a
  selection contained in ONE settled message body, decided from the row's state
  (`data-ask-selectable`) and never from the characters, so streaming text and a
  drag across two messages get no panel at all.
  **Three things are honestly still open, none of them blocking:**
  **(a) touch is untested.** The panel is driven by `mouseup` plus
  `selectionchange`; a touch selection reaches the second of those, but nothing
  was built for the OS's own selection callout and nothing was tried on a
  touchscreen — this is a desktop app, so it was left rather than guessed at.
  **(b) There is no cap on how much you may quote.** A selection cannot cross a
  message, so the ceiling is one message's body; truncating a person's own quote
  silently seemed worse than a long draft they can see and edit.
  **(c) The seed REPLACES whatever was in the composer**, because that is what
  `composerSeed` has always done (a suggestion chip does it too). Quoting on top
  of a half-typed sentence loses the sentence.
- ~~**Continue: truncation-aware only.** A button beside Retry that appears when
  the provider's stop reason says the response hit its output cap, and asks the
  model to resume.~~ **BUILT 2026-08-22, in the judged shape.**
  `ProviderCapabilities.truncation_finish_reasons` holds each provider's own
  spelling of "the answer ran out of output room" (Anthropic `max_tokens`,
  OpenAI and the custom OpenAI-compatible server `length`, Gemini `MAX_TOKENS`,
  Ollama `length`); the orchestrator tests membership against the ANSWERING
  provider's declaration and never names a provider or a spelling, and reports
  the result through the existing `on_answered` seam, so the reply's
  `answeredWith` gains one boolean and the thread grows one 12px accent action
  under the last answer. Three adapters were LOSING the fact before anything could
  read it and were fixed in the same change: Gemini dropped `finishReason`
  entirely, Ollama dropped `done_reason`, and OpenAI's non-streaming path
  collapsed its `finish_reason` to `"stop"` while its streamed path kept it.
  Deliberately still NOT an always-present "make it longer" (§7.9.1 keeps the
  command set short, and a generic Continue invites padding rather than
  completing a cut-off answer). **What it honestly does not do:**
  - **A provider that never reports a cap is never offered.** The declaration
    defaults to empty and every step fails toward silence — an undeclared
    provider, a `capabilities()` that raises (Ollama's is a live HTTP call), a
    reason nobody declared. A cut-off answer from such a provider looks exactly
    like a finished one, which is the harmless failure; the harmful one would be
    offering to continue an answer that was complete.
  - **A resumed answer is TWO messages, not a repaired one.** The fixed sentence
    goes into the thread as an ordinary message from the person, and what comes
    back is a new answer under it. Nothing is spliced onto the end of the first:
    guessing where one stopped and the other began would silently rewrite what
    someone was told.
  - **Only the FINAL answer can claim it.** A mid-loop tool round that hit its cap
    makes no claim — the person is reading the last round's prose, so an offer to
    carry on would resume from text they never saw.
  - **Nothing tells the model where it stopped.** The resume message asks in plain
    words; whether the model picks up cleanly, repeats itself, or starts over is
    the model's business, and the answer that was cut off stays on screen either
    way.
  - **Reopening a chat loses the offer.** The fact rides on the reply, not on the
    stored message row, so a conversation loaded from history never shows it — the
    same property the free-model chip has, and fail-closed in the same direction.
  - **No disclosure line rides with it.** Decided rather than skipped: an answer
    that stops mid-sentence shows that by itself, and a second rail-and-label
    annotation would compete with the free-model chip on exactly the messages
    likeliest to carry both.
- **Knowledge: retrieval over person-attached files. v2. Its screening
  prerequisite is now met** (screening shipped 2026-08-13,
  [untrusted-screening-plan.md](untrusted-screening-plan.md)), **with one thing to
  settle when this is built**: retrieved passages are local file content, and
  decision 5 of that day says local file reads are not screened for now. A
  knowledge base is exactly the case that decision names as the reason to revisit.
  The suggestion is right that
  "search the file" beats "read the whole file", and nothing on any roadmap does
  retrieval. The clean shape when it comes: files enter through the existing picker
  consent; indexing is local-only (an embedding model through the Ollama path that
  already exists; cloud embeddings would ship file contents to a provider, a new
  privacy surface needing its own plain sentence); the index lives in SQLite; and
  retrieved passages enter context marked with their source. A knowledge base is a
  standing channel for a poisoned document to speak in every session, which was the
  FOURTH trigger written against the screening deferral before that deferral
  expired.
  One structural note so it is not rediscovered: a retrieval TOOL must not import
  `providers/` (module boundary rule), so indexing belongs to an
  orchestrator-owned service and the tool only queries the index it left behind.
- **Per-task model assignment: Developer-only, not v1 (owner, 2026-08-09).** The
  raw suggestion was "models casually on the sidebar"; rejected for Simple (model
  choice is a power-user surface, design-doc §7.3.3, and the companion keeps its
  one prefer-quality/prefer-free toggle), it grew into something better in review:
  assigning different models to different KINDS of work (one model drives tool use
  such as search, a different one interprets what came back), with a possible
  drag-and-drop rail of chosen models as the Developer surface. It is also the
  MANUAL half of the auto-routing question above ("Auto-routing depth: v2 or
  now?"), so the two are designed as one thing, with the automatic chooser
  arriving later rather than beside it. **The design now has an owner:**
  [`model-assignments-plan.md`](model-assignments-plan.md) (2026-08-09, proposed,
  not scheduled): closed duty set decided structurally, byte-identical behaviour
  when nothing is assigned, and the mid-turn provider boundary stated rather than
  discovered.
- **Notes: no standalone application; add the pieces where they fit (owner,
  2026-08-09).** A notes app inside Addison is a second product, and the two jobs
  it would do are owned already: "a thing I edit and keep" is the `note` widget
  kind, and "things Addison should know" is long-term memory (design-doc §7.6),
  which has a consent-and-deletion story a free-form pile would not. What survives
  of the suggestion is attachment ("this note is context for this conversation"),
  and that is the Knowledge entry above wearing a smaller hat: once retrieval
  lands, a note is a small attachable document that is already local and already
  trusted. Until then, nothing to build.
- **Messaging channels: SCHEDULED 2026-08-22, the same day the design was
  written.** The owner asked for phone control of Addison (WhatsApp named), the
  plan was written, and all eleven of its owner decisions were answered that
  evening — Telegram default with plural adapters, phases 1–3 scheduled, phase 4
  (approving actions from a phone) deferred toward a bespoke phone app, one
  enabled channel in v1, and two pieces of added scope recorded in the plan (the
  menu-bar popup chat, which needs its own design section before build, and the
  queue-or-decline sleep setting). [`messaging-channel-plan.md`](messaging-channel-plan.md)
  owns it: outbound-only transport with no
  listener of any kind, Telegram's Bot API first behind a plural adapter protocol,
  pairing as the authorization boundary (the automation-nonce code, shown on the
  desktop and sent from the phone) with unknown senders ignored in silence, and a
  **remote floor** that is a closed set of tool ids asserted to be a subset of
  `visible_tools(SAFE)` — so a phone is never offered a tool Simple could not be
  offered. It states the four floors up front, the eleven decisions only the owner
  can take (transport order and the WhatsApp-bridge stance, whether the
  card-over-channel phase is ever built, desktop-history access, background mode,
  what is on the floor's list), and the limits that survive success: the
  transport's servers see the conversation, a stolen unlocked phone is a paired
  identity, and the Mac has to be awake — which is where it parts company with
  design-doc §7.10's managed-proxy assumption, deliberately and at a stated cost.
  - **CLOSED 2026-08-22 (phase 2), decided: the account stays `channel-key:{kind}`
    for v1.** Two connections of one transport share one saved token. The plan's
    §3.9 namespaces the keychain account by
    TRANSPORT KIND — `channel-key:telegram` — while `channels` permits several rows
    of a kind (owner decision 11 restricts what may be *enabled*, not what may be
    saved). So a second Telegram row's token overwrites the first's, and removing
    either row would take the other's token with it. Phase 1 handles both directions
    honestly rather than silently: the panel says the token is shared when a second
    row of that kind exists, and a removal deletes the keychain item only when the
    last row of its kind goes. **Phase 2 answered the question it left open** — should
    the account be keyed by CHANNEL ID instead? — and the answer is **no, not for
    v1**. The shared-token consequence only becomes REAL when two connections of one
    transport can both be live, and owner decision 11 says exactly one channel may be
    enabled at a time; multi-channel is the v2 feature, and it is the feature that
    would revisit this. Keying by kind is also not obviously wrong on its own merits:
    one bot per transport is the common case, and it keeps the account name readable
    in Keychain Access. The plan's §3.9 records the decision in one sentence, so the
    v2 diff that lifts decision 11 finds it there rather than here.
