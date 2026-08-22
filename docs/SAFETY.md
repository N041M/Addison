# Addison: the safety model

**This file owns the safety model.** The floors, the two policy modes, the guards,
the snapshot/restore subsystem and the SAFE-mode invariants are defined here and
nowhere else. `CLAUDE.md` states the rules in short form and links here for the
reasoning; the design doc and engineering spec describe how their own subsystems
touch these rules but do not restate them.

If you are about to write a sentence about G1–G4 in another file, link instead.
Correcting one floor used to mean editing thirteen files.

*(Assembled 2026-07-27 from `CLAUDE.md`'s "Mode-scoped safety model" section and
the corresponding sections of the 2026-07-20 scope amendment. The text is moved,
not rewritten; the owner decisions and their dates are preserved verbatim.)*

---

## The two policy modes

The safety model is **mode-scoped**. There are two policy modes, and the mode is
**derived 1:1 from the active Profile**. The profile is the single source of
truth, there is no separately-persisted mode (`agent_core/policy.py`,
`mode_for_profile`):

- **Simple profile → SAFE mode**: every SAFE-MODE invariant below holds. It was
  the v1 behaviour byte-for-byte until 2026-08-11, when Simple gained the two
  path-bounded file tools and, with them, one gate change in the strict direction:
  a **destructive** call in SAFE takes the per-invocation card instead of the
  coarse ask-once flow. Everything non-destructive is untouched. Invariant 1 owns
  that decision and its terms.
- **Developer profile → OPEN mode**: "nearly completely open." OPEN mode
  **relaxes** the SAFE-mode invariants as follows: real command execution exists
  (the `run_command` **dev-only** tool, `tools/run_command.py`); a `dev_only` tool
  may register at HIGH **without** an `undo()`; routines and widgets may carry a
  `command` step/kind; and the permission gate **auto-allows non-destructive
  actions, prompting ONLY for destructive ones**. "Open" means *fewer prompts, not
  no gate*: the gate still runs (and logs) on every call.
- **Custom profile → a user-tuned surface** (scope amendment 2026-07-20; deep in
  Settings, behind extra confirmation). The user may loosen/tighten the *prompting*
  guards, **never** the global floors. **TWO guards exist** (`GuardConfig`,
  `policy.py`): the per-invocation destructive card, and the auto-grant scope. The
  amendment also describes a workspace-trust dial and a keyword-gate dial; neither
  is built, and the panel grows them **as those capabilities land, never before**.
  A toggle that controls nothing, in a safety panel, is a lie in the worst possible
  place. (Workspace trust today is granted and revoked per folder, not dialled; the
  keyword gate shipped in step 8 phase 3 and is deliberately NOT tunable, plan
  §5.9.) Turning any guard OFF and
  saving mints an **undeletable snapshot anchor** (which records the app build it
  was minted on, see G4), so weakening safety always leaves a guaranteed way back.

Organizing principle (amendment): **reversible data/config** (endpoints, models,
guards, skills, widgets, routines, all snapshotted and one-action reversible) vs.
**inviolable machinery** (Addison's code and the global floors, never alterable by
user or model). The apparent "users can reconfigure Addison" / "users can't break
Addison" tension resolves here: everything a user or the model can change is
reversible config sitting on the rollback floor (G3).

**Destructive-prompt rule (OPEN mode).** The gate auto-grants a call iff it is
non-destructive; destructive calls raise a permission card **per invocation**:
no prior grant is consulted and none is recorded, so approving one destructive
command never silently authorizes a later one (same or different), whether it
arrives directly, via a routine command step, or via a command widget. The card
carries the exact command text (truncated ~120 chars) so the user knows precisely
what they are approving each time; a "Not now" is honoured for the rest of the
turn (don't-nag), then cleared. Destructiveness is per-call
(`tools/base.call_is_destructive`): `run_command` reports **destructive
unconditionally**. The read-only allowlist that used to classify it was DELETED in
#48 after being defeated three ways, so every command cards, `ls` included (see its
docstring); `write_project_file` reports it too (an overwrite is data loss); any
other tool is destructive iff its tier is HIGH. **Non-destructive** tools keep the
coarse session-grant model in both modes; the per-invocation card belongs to
destructive calls, and since 2026-08-11 that is true **in SAFE as well as OPEN**
(invariant 1's terms: the one SAFE-visible destructive tool is the file write, and
one Allow must not cover every later file).

**Artifact disabling** *(renamed from "artifact hiding"; owner decision
2026-08-06).* Routines/widgets that **need** developer abilities are **listed but
disabled in SAFE mode** (shown, never runnable) and return **untouched** when
Developer mode is active again. Switching modes is always allowed. **Snapshots
are the one exception and it is not negotiable: see "Snapshots are never hidden
by mode" below.**

*"Needs developer abilities" is asked of the ARTIFACT, never of the
`created_in_mode` stamp*: **widgets since 2026-08-06, routines since 2026-08-08
(owner decision; [KNOWN-GAPS.md](KNOWN-GAPS.md) holds the closed entry).** The
stamp records where a thing was born, and the two answers part
company for everything that is perfectly usable in Simple but happened to be made
while Developer was active. Read off the stamp, a shopping-list widget arrived in
Simple disabled, announcing that it "uses developer abilities" (about a widget
that invokes no tool at all) with its boxes frozen. Both halves of that were
false, and the person had no way to tell that from a real refusal. The widget
answer is derived from the validator (`widgets.widget_uses_dev_abilities`: OPEN
accepts it and SAFE does not) plus a look-through for what a launcher POINTS AT,
so a future OPEN-only kind is covered on the day it is added rather than the day
someone remembers this paragraph.

The routine answer is `rpc/routines.py::_routine_needs_dev`, **one function, used
by the list marker AND by `routine.run`'s refusal**, true when the plan carries a
command step *or* when a step names a tool the SAFE view does not hold
(`create_automation`, an `mcp:` tool, ...). The two file tools were the standing
example of that set until 2026-08-11, when they joined the SAFE view, so a
routine that reads or edits a file in a trusted folder now RUNS in Simple, with
the ordinary card per step, instead of waiting for Developer. It takes the registry as well as the
plan, which is why it lives a layer above `routines/` (module boundary, CLAUDE.md
§2), and the widget look-through above asks it rather than answering a second time.
Loosening `routine.run`'s SAFE refusal to this question was an owner decision
(2026-08-08): it is sound because the engine's per-step `dev_only` check is the
enforcement and a command-free routine replays through `visible_tools(SAFE)` with
the gate carding per invocation (invariant 3); a routine still cannot out-permission
the live user. What changed is which question is asked, not what may run.

*They used to be hidden: filtered out of `routine.list` and `widget.list`
entirely.* The refusal was never the problem; losing sight of the work was.
Switching Developer → Simple emptied the library and the rail, and the only
honest reading available to the person was that Addison had deleted what they
made. Nothing said otherwise, because the row that would have said it was the row
being withheld. A profile switch is meant to be freely reversible (that is what
makes "switch back" an acceptable answer to a refusal), and it cannot read as
reversible while it looks destructive.

So the row is listed, visibly inert, carrying the plain sentence dispatch already
refuses it with: *"That routine uses developer abilities, so it's waiting in
Developer profile."* (`rpc/constants.py` holds it once, for both the list and the
refusal, so the surface and the refusal cannot drift into telling two stories.)

**This does not leak developer affordances into Simple.** The concern behind that
rule is Simple *acquiring* developer capability: a control that runs one, a
vocabulary that teaches one, an affordance that invites one. A disabled row has
none: no Run control, and a command widget's command text is not printed in the
Simple rail. What is on screen is a name the person typed themselves, on an
artifact they made themselves, in their own library. Addison surfaces nobody
else's artifacts, and there is nothing here to import or share (Routine
export/import is v2 and deliberately not built).

**The marker is DISPLAY ONLY, and this is the part to get right.** The list
carries `unavailable: {reason, message}` on such a row (absent when usable;
`reason` is an open slug vocabulary (`developer_abilities` today), so a later
cause needs no schema change). It is what the surface SAYS. What actually refuses
is dispatch: `routine.run`'s SAFE check, the routine engine's per-step `dev_only`
refusal, and `widget.run`'s SAFE refusal before it touches the registry. **If the
flag and dispatch ever disagree, dispatch wins**: the absence of a marker is not
a permission, and a stale frontend that offers a Run anyway gets refused exactly
like an honest one. A widget row whose spec is not valid in the profile it is
waiting for is still dropped rather than shown: a disabled row is for work that
is merely waiting, not for a spec nothing can read.

## The four global floors

**These never relax, in ANY mode** (flag any conflict rather
than working around it silently):

- **G1: API keys never reach the frontend/webview or SQLite.** They live in the
  OS keychain, read by the Rust shell / Agent Core only at the moment of use,
  never persisted in Agent Core memory beyond one request, never in SQLite. The
  Rust shell may hold a session-lifetime in-memory cache of provider keys (owner
  decision 2026-07-19; one keychain read/prompt per provider per launch; evicted
  on Remove, gone at exit); the cache never widens where keys can GO (shell
  process memory only). The Setup Assistant relay's keys never exist in this
  repo's runtime: they're external and server-side. **Do not touch this
  machinery.**
- **G2: Addison never triggers itself.** No autonomous self-triggering or
  self-scheduling, in any mode. Addison *may author* automation the OS runs (a
  launchd/cron entry, a watcher script), like Claude Code scaffolding a cron job;
  the OS runs it, Addison never fires itself. Arming a powerful action
  **requires** a user-typed keyword, a **per-automation nonce** Addison shows and
  the person retypes ([step-8-automation-plan.md](step-8-automation-plan.md) §3
  owns it). **Built in step 8 phase 3, 2026-08-07**
  (`agent_core/automation_nonce.py`, `tools/arm_automation.py`, and the shell's
  `automation.rs`, which is the only code that writes `~/Library/LaunchAgents` and
  builds the plist itself from typed fields).
  **G2 is untouched by arming existing**, and the three reasons are worth stating:
  the OS runs the job on its own schedule; **`RunAtLoad` is never set**, so arming
  itself causes no run; and the ceremony is a keystroke Addison cannot supply.
  Alongside it: authoring (`create_automation`, phase 2) and the phase-1 fence,
  which closed the generic paths (the OS-automation directories are un-trustable,
  un-writable under the seatbelt, and refused in commands, as is invoking
  `launchctl`/`crontab`/`at`/`batch`), so the gated path is the ONLY path.
  Because the keyword is
  user-typed, observed/injected content can never supply it, so the nonce is also
  a prompt-injection defense. (Scope amendment 2026-07-20; supersedes the earlier
  "no scheduling in v1" wording.)
  **A SECOND INBOUND EDGE, 2026-08-22, and it is not the keyboard.** The messaging
  channel (phase 2) runs a poll loop that waits on a network read for a message a
  paired person sent from their phone. That is a second edge on a process that
  already had one, not a second author: it hands no callback to a clock, a poll that
  finds nothing produces nothing, every unit of work still traces to a human having
  typed something, and **Addison still never speaks first** — no proactive message,
  no notification, no digest, ever. The loop's target is named in
  `tests/test_g2_no_self_trigger.py`'s reviewed set with that argument;
  [messaging-channel-plan.md](messaging-channel-plan.md) §3.4 owns it at length.
- **G3: Guaranteed rollback (the operative meaning of "safety").** Neither the
  user nor the model can drive Addison into an unrecoverable state. App-state
  **snapshots** (automatic before any risky change, plus **on-command**) always
  allow a one-action **Restore to the last verified-working state**, and the
  restore path is itself unbreakable. Snapshots cover config/DB (settings,
  providers, models, skills, widgets, routines) and **exclude the OS keychain**
  (keys stay put; G1 holds). (New floor, 2026-07-20. **Built** in Phase-2 step 1;
  see "The snapshot / restore subsystem" below.)
  **Scope correction, 2026-07-26; RESOLVED 2026-07-31.** For five days this
  floor was **overclaimed in OPEN**, and the record of why is worth keeping.
  The unbreakability above is enforced *within* the database: two `RAISE(ABORT)`
  triggers, a sidecar copy of every payload, and RPC paths that bypass the gate.
  None of that protected the *files* from the OPEN-mode shell. `run_command` has
  `affected_path = None`, so confinement never governs it and
  `workspace_trust_allows`'s protection of the data dir did not apply; the tool
  ran `shell=True` at `$HOME`. One approved command deleted the database, the
  sidecars and every `undeletable` anchor. The card was real (per invocation,
  exact command text, no grant recorded) and it was the **only** layer, and a
  single layer guarded by human attention is not a floor.

  **Closed by [Phase-2 step 5.5](step-5.5-containment-plan.md) items 1–3.**
  `run_command` no longer executes in the Agent Core at all: it crosses the
  ShellBridge like every other OS effect (§1.3), and the shell runs it under a
  **seatbelt profile generated from the live workspace-trust roots**, with the
  data-dir denies emitted *after* every allow so the floor beats even a trusted
  root that contains it. Above that sits the hardline denylist
  (`policy.command_denied_path`), refusing the direct ask before the gate is
  consulted at all three dispatch sites. The headline test,
  `an_approved_command_cannot_delete_the_recovery_floor`, in
  `shell/src-tauri/src/exec.rs`, is **live and mutation-proven**, not an `xfail`.

  Two edges this floor does not reach, and they must not be rounded off: a
  platform with no profile, and Addison's own **code** as opposed to its **data**.
  Both are live items in [`KNOWN-GAPS.md`](KNOWN-GAPS.md), which owns them and
  states each in full; design-doc §9.x states them as threat-model boundaries.
  Neither is written out a third time here.

  This correction followed the one G4 took when "captures the app binary" was
  narrowed to a build reference: **the repo must not carry a floor its own tests
  do not cover.** The difference is which way it was settled: G4 narrowed the
  sentence, G3 got the code.
- **G4: Undeletable anchor on weakening** (≡ what the other docs call *the
  undeletable-anchor rule*; use **G4** in code, comments, and test names).
  Turning a guard OFF in Custom mode (and saving) mints a **permanent,
  undeletable** snapshot anchor that **records the app build it was minted on**:
  lowering your own protections always leaves a guaranteed way back to a working
  **configuration**. (Owner decision 2026-07-20; this corrects the earlier
  wording, which promised the anchor "captures the app binary". What ships is a
  short build **reference** string in `binary_ref` (`{"version", "identifier"}`,
  never bytes, never a path); a restore whose build differs says so in plain
  language and changes settings only. **Restoring a previous app *binary* is not
  implemented** and is tracked as a **Phase-3 updater** item: `updater.rs` is an
  unwired stub, and a second binary-replacement mechanism inside the recovery
  floor would collide with it. The repo must not carry a floor its own tests do
  not cover; that is the anti-pattern the amendment was written against, so the
  promise was narrowed to what the code does.)

## The snapshot / restore subsystem (G3: shipped, Phase-2 step 1)

`agent_core/snapshots/snapshot_manager.py` + the `config_snapshots` table. **Not**
the `UndoManager` beside it: `UndoManager` reverses ONE tool call
(`action_snapshots`, §4.5); this restores Addison's whole mutable **configuration**.
Complementary, independent, and they never call each other. Verbs are
**capture / restore / mint_anchor / prune**, never `record` / `undo_last`.

The single most important property, and the one every change to this code is
judged against: **restore still works when everything else is broken.** That is
why the manager imports stdlib plus two schema-mirroring leaf modules and nothing
else (no provider, no router, no profile, no policy mode, no registry, no gate);
why retention and payload version are module constants rather than settings (so
the model cannot shrink the rollback window); and why every payload is written
**twice**, into the row and into a plain JSON sidecar at
`<db_dir>/snapshots/<id>.json` (dir `0700`, files `0600`), so a damaged database
is recoverable with no SQLite at all. `snapshot.list` and
`snapshot.restoreLastWorking` are the only two RPC methods **exempt** from the
build-failure short-circuit in `main.py`: with a broken store they are answered
from the sidecars, and a restore renames the damaged file aside (never deletes it)
and rebuilds in the same session.

- **Restore is an RPC path, never a registry tool, and never passes the
  `PermissionGate`**: a gate that could deny a restore would make "the restore
  path is itself unbreakable" false. The only model-facing snapshot surface that
  will ever exist is the **LOW, capture-only** `snapshot_now` tool, which **shipped**
  (`agent_core/tools/snapshot_now.py`, in `_V1_TOOL_IDS`): it may
  only ever ADD a row, never restore and never delete, and an AST source test holds
  it to `capture` alone.
- **What is captured** is a declared table set *and* a declared column set
  (`agent_core/snapshots/scope.py`). Tests fail the build if any schema table, or
  any column of a captured table, is neither captured nor explicitly excluded.
  Because restore is replace-all, an uncaptured new column would be silently reset
  to its default **by the recovery path**. Add a Phase-2 table or column, and you
  decide there, in code.
- **Never captured:** the keychain (G1), the transcript, `usage_log`,
  `action_snapshots`, `routine_runs`, `device_identity`, `config_snapshots`
  itself, **`tool_grants`**, and (step 5) **`workspace_trust`**: live consent
  state, not config, and restoring it could reinstate a grant the user had revoked,
  i.e. a privilege grant delivered by a deliberately ungated one-action button. A
  restore additionally clears the live in-session grants. **This inverts the scope
  amendment §8.2's "trust is snapshotted" wording**, which is now annotated there
  as superseded: workspace trust is standing consent that suppresses cards inside
  a directory, so it is a grant in everything but name, and a recovery floor must
  not be a privilege-escalation vector.
- **Permanence lives in the DATABASE.** Two `RAISE(ABORT)` triggers refuse to
  delete an `undeletable = 1` row and refuse to clear the flag, not a `WHERE`
  clause someone can forget. Three kinds of row carry it: the G4 anchor
  (`reason='guard_weakened'`, step 2) and the two possible **bottom rows**, which
  differ by how Addison arrived at this database.
- **The bottom of the restore walk is not the same row on every install.** On a
  **fresh install** it is **genesis** (`reason='genesis'`), written
  `verified_working = 1` (a brand-new install is a configuration that works), so
  the walk has a guaranteed floor from before the first turn ever runs. On an
  **upgraded install** (any database predating this subsystem: `config_snapshots`
  is empty, but the config is not) the bottom row is **`pre_upgrade`** instead,
  and it is **captured unverified**. Nothing has run against it under this
  subsystem's own eyes, and it is a copy of whatever the user has *right now*,
  up to and including the broken setup they may be about to need rescuing from.
  So it starts out unreachable by the one-action button, and there is exactly one
  way for that to change:

  **The rule (amended 2026-07-20 by `4c7ae78`, and this paragraph is the
  authority; earlier wording said the opposite).** `verified_working` means *a
  turn demonstrably answered against these exact bytes*, and nothing else.
  `mark_verified_working()` ordinarily writes a **new** `turn_verified` row. It
  flips the flag on an existing row in **one** narrowed case: a **permanent**
  (`undeletable`) row whose payload fingerprint matches the current config **byte
  for byte** (`_permanent_row_matching`). That match is evidence, not a guess
  (the turn ran against precisely that content), so a fingerprint-proven
  `pre_upgrade` **does** become a one-action target. Ordinary pre-change rows are
  never flagged after the fact, in any circumstance; widening past `undeletable`
  would make "restore lands somewhere that actually ran" false, which is the
  failure G3 exists to prevent.

  **Why this is honest rather than a weakening.** The old rule denied the flag to
  the one row retention can never prune and the triggers refuse to delete, so the
  row most worth returning to was the only row that could never be proven, however
  many turns ran against its exact contents. It did not protect the user, because
  the very next line wrote a `turn_verified` **clone holding identical bytes**,
  and the button restored that instead: the user got the same configuration either
  way, and the only difference was which row was named. Meanwhile the refusal copy
  (*"Addison never saw that one working"*) had become false in the production
  case. The two protections that actually carry the weight are untouched: (1) the
  flag still requires a **completed turn** against those bytes, and (2)
  `restore_last_working()` **skips any row whose fingerprint matches the current
  config**, so this row can never hand back the setup the user is sitting on. The
  restore copy also stays `pre_upgrade`-specific (`_RESTORED_DETAIL`), never the
  generic "last working setup" sentence, so the honesty concern above is answered
  by the copy rather than by keeping the row unprovable.

  Two consequences follow:
  - **On an upgraded install the walk still has no target until the first turn
    completes**, and after that first turn the target may be the `pre_upgrade`
    row itself. Once verified rows exist and are exhausted, the walk stops *above*
    any remaining unverified row and **names** it rather than restoring it
    (`_OLDER_IN_THE_LIST`): the row is on the user's screen, so claiming there is
    nothing further back would be false. Note that `_OLDER_IN_THE_LIST` is now
    **rarely reached on an upgraded install**: once the permanent bottom row is
    verified, nothing sits below it and the walk ends on the honest
    `_AT_THE_BOTTOM` instead. Both branches are still correct; only the traffic
    moved.
  - **The disk arm will still apply it, as an explicitly-labelled last resort.**
    Before any verified row exists (walk outcome `'none'`, the state an upgraded
    install is in until a turn completes) `restore_last_working()` restores
    `pre_upgrade` and says exactly that: *"Addison couldn't find a setup it had
    seen working, so it went back to the most recent settings it had saved
    instead. Have a look and check things are how you want them."*
    (`_RESTORED_UNVERIFIED`). This is deliberate; see the rationale on
    `select_payload_to_restore`: *"nothing at all" is a worse answer than "the most
    recent settings I had, and I said so."* An unverified restore is never
    presented as a verified one; that dishonesty is the failure the floor was
    written against, not the restore itself.

  Which install this is is **measured, not inferred**. `main.py` checks whether
  the database file existed immediately before opening it and passes the answer
  to `SnapshotManager(created_the_database=...)`. Three outcomes, not two: `True`,
  `False`, and `None` for "couldn't find out", and only `True` mints a verified
  `genesis`, so an unknown can never produce a permanent, undeletable restore
  point that claims to be a fresh install. An earlier heuristic inferred this
  from the config row-image and was **deleted**: it read only providers, skills,
  routines and a non-default profile, so a companion with tuned settings, widgets
  and months of chats (the ordinary state of a user who never opens Settings)
  was classified fresh, and the floor handed their broken config back under copy
  promising it had been cleared.
- **`reason` is a closed slug vocabulary** (`REASONS`), never free text: it is
  written by auto-hooks and, later, by model-orchestrated flows, and free text
  would let model-authored prose into the config store. Unknown slugs collapse to
  `other`.
- **Restore targets the last *verified-working* config, not "before the last
  edit"**, so it always lands somewhere that actually ran. A row is verified
  once a turn completed against it. `restore_last_working()` never targets a
  config identical to the present one, so **each click steps back one distinct
  proven configuration**; two bad changes deep, the user clicks twice. Retention
  is 50 snapshots / 30 days (whichever keeps more), with anchors and the newest
  **two** verified rows exempt **in the SQL**; a rule that could prune the last
  verified rows would switch G3 off with no error anywhere. Two, not one, and the
  second is not slack: the restore walk skips any verified row whose fingerprint
  matches the *current* config (restoring it would change zero bytes), so if only
  the newest verified row were exempt, the one surviving row could be exactly the
  row the walk skips, leaving the floor with no target at all.

**Snapshots are never hidden by mode (C6, a deliberate override).**
`created_in_mode` ships on `config_snapshots`, but it is **recorded for display
only**. No list, restore, prune, or delete query may filter on it, in any mode.
The engineering spec's DDL comment said this column "mirrors existing artifact
hiding"; that phrasing was **overridden, not followed**. Taken literally it hides
the way back from exactly the user who most needs it: weakened a guard in Custom,
broke things, switched to Simple, opens Restore points and sees an empty list.
That is a larger threat to G3 than any question in the amendment's §13. Two tests
hold the line: a behavioural one (rows made in every mode restore under SAFE) and
a **source-level** one, `test_no_snapshot_query_filters_on_created_in_mode`, which
reads the SQL in `store.py` and `snapshot_manager.py` and fails if the column ever
appears in a filter position. The behavioural test alone would only prove today's
behaviour; it would not stop someone adding `AND created_in_mode = ?` next quarter.

## SAFE-mode invariants (Simple profile, hold byte-for-byte)

These are hard constraints in SAFE mode. If a SAFE-mode request appears to
conflict with one, **flag it rather than working around it silently.** OPEN mode
relaxes exactly these four, and only as spelled out above.

1. **No arbitrary code/shell execution.** SAFE-view tools are individual typed
   functions, not "run command"; SAFE routines are *declarative plans* (§6.1),
   not scripts. Do not add `eval`, a Lua sandbox, or a raw-code field. (OPEN mode's
   `run_command` is a single **dev-only** tool, absent from the SAFE registry view
   (`registry.visible_tools(SAFE)`), and it refuses to run under SAFE as a belt.
   The automation tools (`create_automation`, `arm_automation`,
   `disarm_automation`) are `open_only` too: also absent from the SAFE view, also
   refused at dispatch outside OPEN. Step 7's discovered tool-server tools register
   the same way and are therefore in the same position: `open_only`, so
   `visible_tools(SAFE)` has never held one, and refused at both dispatch sites
   outside OPEN. Addison calls a tool server over HTTP and never launches one, so
   nothing in that step starts a process either.)

   **Step 5's `read_project_file` / `write_project_file` are IN the SAFE view since
   2026-08-11, and this invariant is unaffected**: they are typed, path-bounded
   functions, not a shell. **Owner decision, 2026-08-11.** They were `open_only`
   until then, and the effect on the profile the personas use was that Addison
   could not change an existing file at all: asked to fix a line in a document, it
   refused and offered to save a *new* file beside it. That is the defect, not the
   safety model, so Simple gained the capability on the terms the safety model
   already had for it:

   - **the card comes first, every time.** A write is destructive
     (`is_destructive` → True unconditionally), and in SAFE a destructive call now
     takes the **per-invocation** card rather than the coarse ask-once flow
     (`permissions/gate.py`), so each edit is announced by name and no approval
     carries over to the next one. The wording is the tool's own
     (`permission_sentence`): it names the file and says the change can be undone.
     This is a **tightening** of the SAFE gate: nothing that used to card stopped
     carding, and every non-destructive SAFE call runs the coarse flow exactly as
     before;
   - **nothing else moved.** Confinement to a currently-trusted root is unchanged
     and still refuses *before* the gate, so there is no card that can approve a
     path outside one; the shell still refuses Addison's own data directory,
     binary files and oversize prior content; the symlink/hard-link identity checks
     are untouched; and the write is undoable, which is what lets it sit in a view
     where invariant 2 applies in full (it never took the `allow_missing_undo`
     waiver; see invariant 2).

   **What this costs, said plainly:** design-doc §9's *"filesystem scope by picker,
   not by path"* no longer describes the whole of Simple. Simple's own tools still
   scope by picker (`read_file`, `save_file`); these two scope by **trusted root**.

   **The follow-up this left open is CLOSED (owner decision, 2026-08-12): Simple
   has the "Folders Addison may work in" panel.** For a day it did not: the panel
   was Developer/Custom only, so the capability reached a Simple person only for
   folders trusted while Developer was active, and a Simple-only person could not
   grant one at all. The panel now renders in every profile
   (`shell/src/components/SettingsPage.tsx`), and the Tools surface lists trusted
   folders in every profile with it. **Nothing about the ceremony was relaxed to do
   it**: the same two steps in every profile (the OS folder picker, then Addison's
   own inline "Trust this folder?" confirm) and the same core-side floors behind
   them (the data-dir refusal, the automation-dir refusal, absolute paths). What
   differs by profile is the *copy*, because the truth differs: in OPEN the panel
   says Addison reads and edits without asking first, and in SAFE it says Addison
   asks before every change, which is what the per-invocation card actually does.
2. **Every `risk_tier != LOW` tool must have a real `undo()`**, enforced at
   registration in `tools/registry.py` (it raises otherwise). Do NOT satisfy this
   with a no-op `undo()`; a tool that genuinely can't be undone stays LOW and
   read-only. This registration check is the single most important test in the
   codebase (spec §9). (The ONLY exception is an **`allow_missing_undo`**
   registration, which is never in the SAFE view; it exists solely for OPEN mode.
   *Naming precision added 2026-07-27:* this said `dev_only`, which step 5 split
   into two independent dimensions, `open_only` for visibility and
   `allow_missing_undo` for the exemption. `dev_only=True` survives as a
   convenience alias setting both, so the old wording was not wrong, but the
   exemption is the second flag and `registry.py` is the authority.)
3. **A Routine never gets permissions beyond what the user granted live**: no
   privilege escalation via automation. It uses the *same* `ToolRegistry` and
   `PermissionGate` instances as the live orchestrator, in **both** modes: the
   SAFE/OPEN distinction is a *filtered view* over the one shared registry
   (`visible_tools(mode)`), never a second registry, so this no-escalation
   property survives OPEN mode intact.
   **A second filtered view arrived on 2026-08-22** and it is the same shape applied
   to a second SURFACE rather than a second caller: `remote_tools(mode)` is what a
   turn that came from a paired phone is offered, an INTERSECTION with
   `visible_tools(mode)` and therefore a subset of the desk's view in every mode — *a
   remote turn is never offered a tool Simple could not be offered.* It is empty in
   phase 2 by design. Like the routine view it is a filter over the one registry, and
   like the routine view its marker is not its enforcement: `refuse_if_not_remote` at
   both dispatch paths is.
   [messaging-channel-plan.md](messaging-channel-plan.md) §3.6 owns the closed set
   and why it is a list of ids rather than a tier test.
4. **Widgets are capability-gated, not code, and buildable in every mode (scope
   amendment 2026-07-20).** Widgets can be *built* in all modes; the mode gates
   the *capability*, not the ability to build. SAFE-tier widgets come from a
   **safe, non-destructive vocabulary** (`agent_core/widgets.py`), and since
   2026-08-06 that vocabulary is a **CLOSED SET OF KINDS, hard-coded in that
   file**, five in SAFE:

   - the two launchers. `{kind:"routine",routineId,title}` runs a saved routine
     through the *existing* routine.run path (same registry + gate, zero new
     execution surface), and `{kind:"stat",source,title}` reads the fixed
     whitelist `tokens_month` / `provider_latency` / `connections`;
   - the three interactive kinds (`{kind:"checklist",items,title}`,
     `{kind:"note",text,title}`, `{kind:"timer",seconds,title}`), rendered by
     *trusted Addison components*, backed by Addison's own storage, invoking **no
     tool at all**. Their mutable half (a tick, an edited note, a paused timer)
     lives in the separate `widget_state` table, validated per kind server-side at
     write *and* at read, and carries **no permission card** because there is
     nothing there to gate: they are non-destructive by construction, which is
     what this invariant asks for. Nothing counts a timer down but the frontend
     and nothing fires at zero; G2 is untouched.

   **There is no capability-declaration lattice, and that is the decision, not a
   gap** (owner decision 2026-08-06). The amendment sketched a
   `required_capabilities` field plus a capability→minimum-mode map; the closed
   list of kinds *is* that gate, and it cannot drift from what the code does,
   because it is what the code does. A spec declaring its own powers would be the
   saved data telling the app what it is allowed to do.

   Still **no eval, no arbitrary code, no raw-code/template field**: SAFE-1
   and the webview CSP hold; a SAFE widget can never reach anything that harms the
   machine or Addison. Unknown kinds/sources are rejected at save and hidden at
   render. Higher tiers (Developer/Custom) add **code-backed / system-capable**
   widgets (today's OPEN `{kind:"command",…}`; monitors/scripts under
   workspace-trust + undo + snapshot + keyword gate). Surviving guarantee: a
   widget never exceeds its mode's tier, and SAFE widgets are non-destructive by
   construction.
