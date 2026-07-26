# Addison — the safety model

**This file owns the safety model.** The floors, the two policy modes, the guards,
the snapshot/restore subsystem and the SAFE-mode invariants are defined here and
nowhere else. `CLAUDE.md` states the rules in short form and links here for the
reasoning; the design doc and engineering spec describe how their own subsystems
touch these rules but do not restate them.

If you are about to write a sentence about G1–G4 in another file, link instead.
Correcting one floor used to mean editing thirteen files.

*(Assembled 2026-07-27 from `CLAUDE.md`'s "Mode-scoped safety model" section and
the corresponding sections of the 2026-07-20 scope amendment. The text is moved,
not rewritten — the owner decisions and their dates are preserved verbatim.)*

---

## The two policy modes

The safety model is **mode-scoped**. There are two policy modes, and the mode is
**derived 1:1 from the active Profile** — the profile is the single source of
truth, there is no separately-persisted mode (`agent_core/policy.py`,
`mode_for_profile`):

- **Simple profile → SAFE mode** — today's behaviour, **byte-for-byte**. Every
  SAFE-MODE invariant below holds.
- **Developer profile → OPEN mode** — "nearly completely open." OPEN mode
  **relaxes** the SAFE-mode invariants as follows: real command execution exists
  (the `run_command` **dev-only** tool, `tools/run_command.py`); a `dev_only` tool
  may register at HIGH **without** an `undo()`; routines and widgets may carry a
  `command` step/kind; and the permission gate **auto-allows non-destructive
  actions, prompting ONLY for destructive ones**. "Open" means *fewer prompts, not
  no gate* — the gate still runs (and logs) on every call.
- **Custom profile → a user-tuned surface** (scope amendment 2026-07-20; deep in
  Settings, behind extra confirmation). The user may loosen/tighten the *prompting*
  guards — **never** the global floors. **TWO guards exist** (`GuardConfig`,
  `policy.py`): the per-invocation destructive card, and the auto-grant scope. The
  amendment also describes a workspace-trust dial and a keyword-gate dial; neither
  is built, and the panel grows them **as those capabilities land, never before** —
  a toggle that controls nothing, in a safety panel, is a lie in the worst possible
  place. (Workspace trust today is granted and revoked per folder, not dialled; the
  keyword gate is Phase-2 step 8 and does not exist.) Turning any guard OFF and
  saving mints an **undeletable snapshot anchor** (which records the app build it
  was minted on — see G4), so weakening safety always leaves a guaranteed way back.

Organizing principle (amendment): **reversible data/config** (endpoints, models,
guards, skills, widgets, routines — all snapshotted and one-action reversible) vs.
**inviolable machinery** (Addison's code and the global floors, never alterable by
user or model). The apparent "users can reconfigure Addison" / "users can't break
Addison" tension resolves here: everything a user or the model can change is
reversible config sitting on the rollback floor (G3).

**Destructive-prompt rule (OPEN mode).** The gate auto-grants a call iff it is
non-destructive; destructive calls raise a permission card **per invocation** —
no prior grant is consulted and none is recorded, so approving one destructive
command never silently authorizes a later one (same or different), whether it
arrives directly, via a routine command step, or via a command widget. The card
carries the exact command text (truncated ~120 chars) so the user knows precisely
what they are approving each time; a "Not now" is honoured for the rest of the
turn (don't-nag), then cleared. Destructiveness is per-call
(`tools/base.call_is_destructive`): `run_command` reports **destructive
unconditionally** — the read-only allowlist that used to classify it was DELETED in
#48 after being defeated three ways, so every command cards, `ls` included (see its
docstring); any other tool is destructive iff its tier is HIGH. Normal (non-dev) tools keep the coarse
session-grant model in both modes — per-invocation is specific to destructive dev
actions.

**Artifact hiding.** Routines/widgets created in OPEN mode (`created_in_mode`
column) are **hidden and disabled in SAFE mode** — never listed, never runnable —
and return **untouched** when Developer mode is active again. Switching modes is
always allowed. **Snapshots are the one exception and it is not negotiable — see
"Snapshots are never hidden by mode" below.**

## The four global floors

**These never relax, in ANY mode** (flag any conflict rather
than working around it silently):

- **G1 — API keys never reach the frontend/webview or SQLite.** They live in the
  OS keychain, read by the Rust shell / Agent Core only at the moment of use,
  never persisted in Agent Core memory beyond one request, never in SQLite. The
  Rust shell may hold a session-lifetime in-memory cache of provider keys (owner
  decision 2026-07-19 — one keychain read/prompt per provider per launch; evicted
  on Remove, gone at exit); the cache never widens where keys can GO (shell
  process memory only). The Setup Assistant relay's keys never exist in this
  repo's runtime — they're external and server-side. **Do not touch this
  machinery.**
- **G2 — Addison never triggers itself.** No autonomous self-triggering or
  self-scheduling, in any mode. Addison *may author* automation the OS runs (a
  launchd/cron entry, a watcher script) — like Claude Code scaffolding a cron job;
  the OS runs it, Addison never fires itself. Running/arming a powerful action
  **will require** a **user-typed keyword prefix** (e.g. `!run …`) — designed, and
  **not built**: it is Phase-2 step 8 and there is no keyword-gate code in the tree,
  so nothing today can author or arm automation. Because it is
  user-typed, observed/injected content can never supply it, so the prefix is also
  a prompt-injection defense. (Scope amendment 2026-07-20; supersedes the earlier
  "no scheduling in v1" wording.)
- **G3 — Guaranteed rollback (the operative meaning of "safety").** Neither the
  user nor the model can drive Addison into an unrecoverable state. App-state
  **snapshots** — automatic before any risky change, plus **on-command** — always
  allow a one-action **Restore to the last verified-working state**, and the
  restore path is itself unbreakable. Snapshots cover config/DB (settings,
  providers, models, skills, widgets, routines) and **exclude the OS keychain**
  (keys stay put — G1 holds). (New floor, 2026-07-20. **Built** in Phase-2 step 1
  — see "The snapshot / restore subsystem" below.)
  **Scope correction, 2026-07-26 — this holds in SAFE and is currently
  OVERCLAIMED in OPEN.** The unbreakability above is enforced *within* the
  database: two `RAISE(ABORT)` triggers, a sidecar copy of every payload, and RPC
  paths that bypass the gate. None of that protects the *files* from the OPEN-mode
  shell. `run_command` has `affected_path = None`, so confinement never governs it
  and `workspace_trust_allows`'s protection of the data dir does not apply; the
  tool runs `shell=True` at `$HOME`. One approved command therefore deletes the
  database, the sidecars and every `undeletable` anchor. The card is real (per
  invocation, exact command text, no grant recorded) and it is the *only* layer.
  Do not repeat the sentence "the restore path is itself unbreakable" without this
  qualifier until **[Phase-2 step 5.5](step-5.5-containment-plan.md)** lands.
  This is the same correction G4 took when "captures the app binary" was narrowed
  to a build reference: **the repo must not carry a floor its own tests do not
  cover.**
- **G4 — Undeletable anchor on weakening** (≡ what the other docs call *the
  undeletable-anchor rule*; use **G4** in code, comments, and test names).
  Turning a guard OFF in Custom mode (and saving) mints a **permanent,
  undeletable** snapshot anchor that **records the app build it was minted on** —
  lowering your own protections always leaves a guaranteed way back to a working
  **configuration**. (Owner decision 2026-07-20 — this corrects the earlier
  wording, which promised the anchor "captures the app binary". What ships is a
  short build **reference** string in `binary_ref` (`{"version", "identifier"}`,
  never bytes, never a path); a restore whose build differs says so in plain
  language and changes settings only. **Restoring a previous app *binary* is not
  implemented** and is tracked as a **Phase-3 updater** item — `updater.rs` is an
  unwired stub, and a second binary-replacement mechanism inside the recovery
  floor would collide with it. The repo must not carry a floor its own tests do
  not cover; that is the anti-pattern the amendment was written against, so the
  promise was narrowed to what the code does.)

## The snapshot / restore subsystem (G3 — shipped, Phase-2 step 1)

`agent_core/snapshots/snapshot_manager.py` + the `config_snapshots` table. **Not**
the `UndoManager` beside it: `UndoManager` reverses ONE tool call
(`action_snapshots`, §4.5); this restores Addison's whole mutable **configuration**.
Complementary, independent, and they never call each other. Verbs are
**capture / restore / mint_anchor / prune** — never `record` / `undo_last`.

The single most important property, and the one every change to this code is
judged against: **restore still works when everything else is broken.** That is
why the manager imports stdlib plus two schema-mirroring leaf modules and nothing
else — no provider, no router, no profile, no policy mode, no registry, no gate;
why retention and payload version are module constants rather than settings (so
the model cannot shrink the rollback window); and why every payload is written
**twice** — into the row and into a plain JSON sidecar at
`<db_dir>/snapshots/<id>.json` (dir `0700`, files `0600`), so a damaged database
is recoverable with no SQLite at all. `snapshot.list` and
`snapshot.restoreLastWorking` are the only two RPC methods **exempt** from the
build-failure short-circuit in `main.py`: with a broken store they are answered
from the sidecars, and a restore renames the damaged file aside (never deletes it)
and rebuilds in the same session.

- **Restore is an RPC path, never a registry tool, and never passes the
  `PermissionGate`** — a gate that could deny a restore would make "the restore
  path is itself unbreakable" false. The only model-facing snapshot surface that
  will ever exist is the **LOW, capture-only** `snapshot_now` tool, which **shipped**
  (`agent_core/tools/snapshot_now.py`, in `_V1_TOOL_IDS`): it may
  only ever ADD a row, never restore and never delete, and an AST source test holds
  it to `capture` alone.
- **What is captured** is a declared table set *and* a declared column set
  (`agent_core/snapshots/scope.py`). Tests fail the build if any schema table, or
  any column of a captured table, is neither captured nor explicitly excluded —
  because restore is replace-all, an uncaptured new column would be silently reset
  to its default **by the recovery path**. Add a Phase-2 table or column, and you
  decide there, in code.
- **Never captured:** the keychain (G1), the transcript, `usage_log`,
  `action_snapshots`, `routine_runs`, `device_identity`, `config_snapshots`
  itself, **`tool_grants`**, and (step 5) **`workspace_trust`** — live consent
  state, not config: restoring it could reinstate a grant the user had revoked,
  i.e. a privilege grant delivered by a deliberately ungated one-action button. A
  restore additionally clears the live in-session grants. **This inverts the scope
  amendment §8.2's "trust is snapshotted" wording**, which is now annotated there
  as superseded: workspace trust is standing consent that suppresses cards inside
  a directory, so it is a grant in everything but name, and a recovery floor must
  not be a privilege-escalation vector.
- **Permanence lives in the DATABASE.** Two `RAISE(ABORT)` triggers refuse to
  delete an `undeletable = 1` row and refuse to clear the flag — not a `WHERE`
  clause someone can forget. Three kinds of row carry it: the G4 anchor
  (`reason='guard_weakened'`, step 2) and the two possible **bottom rows**, which
  differ by how Addison arrived at this database.
- **The bottom of the restore walk is not the same row on every install.** On a
  **fresh install** it is **genesis** (`reason='genesis'`), written
  `verified_working = 1` — a brand-new install is a configuration that works — so
  the walk has a guaranteed floor from before the first turn ever runs. On an
  **upgraded install** (any database predating this subsystem: `config_snapshots`
  is empty, but the config is not) the bottom row is **`pre_upgrade`** instead,
  and it is **captured unverified**. Nothing has run against it under this
  subsystem's own eyes, and it is a copy of whatever the user has *right now* —
  up to and including the broken setup they may be about to need rescuing from.
  So it starts out unreachable by the one-action button, and there is exactly one
  way for that to change:

  **The rule (amended 2026-07-20 by `4c7ae78`, and this paragraph is the
  authority — earlier wording said the opposite).** `verified_working` means *a
  turn demonstrably answered against these exact bytes*, and nothing else.
  `mark_verified_working()` ordinarily writes a **new** `turn_verified` row. It
  flips the flag on an existing row in **one** narrowed case: a **permanent**
  (`undeletable`) row whose payload fingerprint matches the current config **byte
  for byte** (`_permanent_row_matching`). That match is evidence, not a guess —
  the turn ran against precisely that content — so a fingerprint-proven
  `pre_upgrade` **does** become a one-action target. Ordinary pre-change rows are
  never flagged after the fact, in any circumstance; widening past `undeletable`
  would make "restore lands somewhere that actually ran" false, which is the
  failure G3 exists to prevent.

  **Why this is honest rather than a weakening.** The old rule denied the flag to
  the one row retention can never prune and the triggers refuse to delete — so the
  row most worth returning to was the only row that could never be proven, however
  many turns ran against its exact contents. It did not protect the user, because
  the very next line wrote a `turn_verified` **clone holding identical bytes**,
  and the button restored that instead: the user got the same configuration either
  way, and the only difference was which row was named. Meanwhile the refusal copy
  — *"Addison never saw that one working"* — had become false in the production
  case. The two protections that actually carry the weight are untouched: (1) the
  flag still requires a **completed turn** against those bytes, and (2)
  `restore_last_working()` **skips any row whose fingerprint matches the current
  config**, so this row can never hand back the setup the user is sitting on. The
  restore copy also stays `pre_upgrade`-specific (`_RESTORED_DETAIL`), never the
  generic "last working setup" sentence, so the honesty concern above is answered
  by the copy rather than by keeping the row unprovable.

  Two consequences follow:
  - **On an upgraded install the walk still has no target until the first turn
    completes** — and after that first turn the target may be the `pre_upgrade`
    row itself. Once verified rows exist and are exhausted, the walk stops *above*
    any remaining unverified row and **names** it rather than restoring it
    (`_OLDER_IN_THE_LIST`) — the row is on the user's screen, so claiming there is
    nothing further back would be false. Note that `_OLDER_IN_THE_LIST` is now
    **rarely reached on an upgraded install**: once the permanent bottom row is
    verified, nothing sits below it and the walk ends on the honest
    `_AT_THE_BOTTOM` instead. Both branches are still correct; only the traffic
    moved.
  - **The disk arm will still apply it, as an explicitly-labelled last resort.**
    Before any verified row exists (walk outcome `'none'` — the state an upgraded
    install is in until a turn completes) `restore_last_working()` restores
    `pre_upgrade` and says exactly that: *"Addison couldn't find a setup it had
    seen working, so it went back to the most recent settings it had saved
    instead. Have a look and check things are how you want them."*
    (`_RESTORED_UNVERIFIED`). This is deliberate — see the rationale on
    `select_payload_to_restore`: *"nothing at all" is a worse answer than "the most
    recent settings I had, and I said so."* An unverified restore is never
    presented as a verified one; that dishonesty is the failure the floor was
    written against, not the restore itself.

  Which install this is is **measured, not inferred**. `main.py` checks whether
  the database file existed immediately before opening it and passes the answer
  to `SnapshotManager(created_the_database=...)`. Three outcomes, not two: `True`,
  `False`, and `None` for "couldn't find out" — and only `True` mints a verified
  `genesis`, so an unknown can never produce a permanent, undeletable restore
  point that claims to be a fresh install. An earlier heuristic inferred this
  from the config row-image and was **deleted**: it read only providers, skills,
  routines and a non-default profile, so a companion with tuned settings, widgets
  and months of chats — the ordinary state of a user who never opens Settings —
  was classified fresh, and the floor handed their broken config back under copy
  promising it had been cleared.
- **`reason` is a closed slug vocabulary** (`REASONS`), never free text — it is
  written by auto-hooks and, later, by model-orchestrated flows, and free text
  would let model-authored prose into the config store. Unknown slugs collapse to
  `other`.
- **Restore targets the last *verified-working* config, not "before the last
  edit"** — so it always lands somewhere that actually ran. A row is verified
  once a turn completed against it. `restore_last_working()` never targets a
  config identical to the present one, so **each click steps back one distinct
  proven configuration**; two bad changes deep, the user clicks twice. Retention
  is 50 snapshots / 30 days (whichever keeps more), with anchors and the newest
  **two** verified rows exempt **in the SQL** — a rule that could prune the last
  verified rows would switch G3 off with no error anywhere. Two, not one, and the
  second is not slack: the restore walk skips any verified row whose fingerprint
  matches the *current* config (restoring it would change zero bytes), so if only
  the newest verified row were exempt, the one surviving row could be exactly the
  row the walk skips — leaving the floor with no target at all.

**Snapshots are never hidden by mode (C6 — a deliberate override).**
`created_in_mode` ships on `config_snapshots`, but it is **recorded for display
only**. No list, restore, prune, or delete query may filter on it, in any mode.
The engineering spec's DDL comment said this column "mirrors existing artifact
hiding"; that phrasing was **overridden, not followed**. Taken literally it hides
the way back from exactly the user who most needs it — weakened a guard in Custom,
broke things, switched to Simple, opens Restore points and sees an empty list.
That is a larger threat to G3 than any question in the amendment's §13. Two tests
hold the line: a behavioural one (rows made in every mode restore under SAFE) and
a **source-level** one, `test_no_snapshot_query_filters_on_created_in_mode`, which
reads the SQL in `store.py` and `snapshot_manager.py` and fails if the column ever
appears in a filter position. The behavioural test alone would only prove today's
behaviour; it would not stop someone adding `AND created_in_mode = ?` next quarter.

## SAFE-mode invariants (Simple profile — hold byte-for-byte)

These are hard constraints in SAFE mode. If a SAFE-mode request appears to
conflict with one, **flag it rather than working around it silently.** OPEN mode
relaxes exactly these four, and only as spelled out above.

1. **No arbitrary code/shell execution.** SAFE-view tools are individual typed
   functions, not "run command"; SAFE routines are *declarative plans* (§6.1),
   not scripts. Do not add `eval`, a Lua sandbox, or a raw-code field. (OPEN mode's
   `run_command` is a single **dev-only** tool, absent from the SAFE registry view
   — `registry.visible_tools(SAFE)` — and it refuses to run under SAFE as a belt.
   Step 5's `read_project_file` / `write_project_file` are `open_only` too: also
   absent from the SAFE view, also refused at dispatch outside OPEN. They are
   typed path-bounded functions, not a shell, so this invariant is unaffected —
   and the SAFE file tools keep design-doc §9's picker scoping unchanged.)
2. **Every `risk_tier != LOW` tool must have a real `undo()`**, enforced at
   registration in `tools/registry.py` (it raises otherwise). Do NOT satisfy this
   with a no-op `undo()` — a tool that genuinely can't be undone stays LOW and
   read-only. This registration check is the single most important test in the
   codebase (spec §9). (The ONLY exception is a `dev_only` registration, which is
   never in the SAFE view; it exists solely for OPEN mode.)
3. **A Routine never gets permissions beyond what the user granted live** — no
   privilege escalation via automation. It uses the *same* `ToolRegistry` and
   `PermissionGate` instances as the live orchestrator, in **both** modes: the
   SAFE/OPEN distinction is a *filtered view* over the one shared registry
   (`visible_tools(mode)`), never a second registry, so this no-escalation
   property survives OPEN mode intact.
4. **Widgets are capability-gated, not code — buildable in every mode (scope
   amendment 2026-07-20).** Widgets can be *built* in all modes; the mode gates
   the *capability*, not the ability to build. SAFE-tier widgets come from a
   **safe, non-destructive vocabulary** (`agent_core/widgets.py`): the launchers
   (`{kind:"routine",routineId,title}` runs a saved routine through the *existing*
   routine.run path — same registry + gate, zero new execution surface;
   `{kind:"stat",source,title}` from the fixed whitelist `tokens_month` /
   `provider_latency` / `connections`) **plus new interactive display kinds**
   (to-do/checklist, note, timer, …) rendered by *trusted Addison components* and
   backed by safe storage. Still **no eval, no arbitrary code, no raw-code/template
   field** — SAFE-1 and the webview CSP hold; a SAFE widget can never reach
   anything that harms the machine or Addison. Unknown kinds/sources are rejected
   at save and hidden at render. Higher tiers (Developer/Custom) add **code-backed
   / system-capable** widgets (today's OPEN `{kind:"command",…}`; monitors/scripts
   under workspace-trust + undo + snapshot + keyword gate). Surviving guarantee: a
   widget never exceeds its mode's tier, and SAFE widgets are non-destructive by
   construction.
