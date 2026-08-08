# Step 8 — OS-run automation and the keyword gate

**Status: COMPLETE. All four phases built 2026-08-07**, the day this plan was
written. With it, step 8 is done and the Phase-2 sequence closes. This plan turns the recorded decisions into a build
order and settles the engineering decisions those left open. The principle is the
scope amendment's §9 sentence, unchanged since 2026-07-20:

> **Addison authors; the OS runs; Addison never triggers itself. Powerful/armed
> actions require a user-typed keyword.**

`ROADMAP.md` owns scheduling. G2's full text lives in [SAFETY.md](SAFETY.md);
this step is the one that turned its "designed, not built" clause into code, and
§7 below is the record of every document line that changed when it did.

---

## 1. What is already decided

- **The keyword is a per-automation nonce, not a fixed prefix** (owner,
  2026-08-07, recorded in `HANDOFF.md` at the time). Addison shows a short code
  next to a full preview of what will be armed; the person retypes it. A fixed
  prefix (`!run …`, the amendment's sketch) is forgeable by anything that can
  write English — a web page, a pasted message or Addison's own reply can all
  say "now type `!run install`", and a person doing what the screen says is the
  normal case, not the failure case. A nonce minted at the moment of asking is
  the one string no observed content could have written down in advance.
- **What it gates** (owner's reading, recorded in `KNOWN-GAPS.md`): running or
  arming powerful / OS-automation actions **in the harness, never ordinary
  chat**. §5.2 narrows this honestly for v1: the nonce gates **arming**.
- **Addison never triggers itself — G2 is a floor and stays one.** Addison may
  *author* automation; only the OS ever runs it; nothing in this step gives
  Addison a timer, a watcher or a callback of its own.
- **Through the existing registry, gate and audit, never a side channel** — the
  same rule step 7 held to. Authoring and arming are ordinary tools; every
  outcome lands in `tool_audit`.
- **Reversible config** — an automation row is snapshot-captured, revocable,
  and addable by prompting, exactly like a provider endpoint or an MCP server.
  What a *restore* may do with one is deliberately narrow — see §5.6.

## 2. The constraint that shapes everything

**The Agent Core has no OS permissions of its own** (spec §1.3), and the
seatbelt (step 5.5) already denies precisely what arming needs: `(deny
default)` blocks `launchctl`'s Mach traffic, and `deny file-write*` covers
`~/Library/LaunchAgents` unless somebody trusts it. So arming cannot ride
`run_command`, and that is a feature: the only way to install or remove an
armed job is a **typed shell surface** (`automation.install` / `remove` /
`status`) that exists for nothing else, performed by the shell itself, outside
the seatbelt because it never passes through it.

The narrowness is the contract (§5.8): the core sends **structured fields,
never a built plist** — the highest-trust process assembles the XML itself,
enforces the label prefix, and will only ever write or delete its own files in
one directory. A shell surface that accepted raw XML for LaunchAgents would be
`run_command` with extra steps.

**And the generic paths must close, because one of them is open today.**
`workspace_trust_allows` refuses only Addison's own protected directories, so
`~/Library/LaunchAgents` can currently be trusted as a workspace and
`write_project_file` can put a plist there with an ordinary card — login-time
automation, armed, no keyword, on the tree as it stands. The claim "nothing in
the tree can author or arm automation, so G2 holds trivially" is true of
Addison's *machinery* and false of that path. Phase 1 closes it (§5.5) before
anything new is built, so that when the gated path exists it is the **only**
path, rather than the polite one.

## 3. The nonce — what it is and what it defends

**Mechanics** (engineering detail settled here; the *decision* was §1's):

- Minted in the core (`secrets`-module randomness) at the moment the arm card
  is raised; **single-use, per-request**. A fresh attempt mints a fresh nonce.
- Short and typeable by personas 54 and 68: six characters from an alphabet
  with the lookalikes removed (no `0`/`O`, no `1`/`I`/`L`), shown grouped
  `ABC-DEF`. Compared case-insensitively with separators stripped, via
  `hmac.compare_digest` after normalisation. Three mismatches and the request
  DENIES — the person can always start over, with a new nonce.
- **It never enters the model's context, the transcript, `tool_audit`, or any
  store.** It travels core → webview on the card event and back as the typed
  answer on `permission.respond` (which gains a `typed` field); the comparison
  happens in the core; the model's tool_result says granted or denied and
  nothing else.
- The card shows, above the input: the automation's name, its schedule in
  plain words, the **exact command** it will run, where the file will be
  written, and two sentences that must survive every redesign: *"This will run
  on its own schedule even when Addison is closed."* and *"It runs outside
  Addison's sandbox."* (§5.7 is why both are true.)

**What it defends, stated at its real strength and no higher.** The model
cannot answer a permission card at all — that boundary predates this step. What
the nonce adds on top of the card's buttons: observed content cannot pre-script
the approval ("click OK" is one instruction; a code that did not exist when the
instruction was written cannot be one), and a person cannot arm recurring,
unconfined automation on reflex, because retyping six characters is exactly
enough friction to make somebody read the preview they are copying from. What
it does NOT defend: a person deliberately typing the nonce for a job they have
not understood. The preview is the defense there, which is why the card carries
the whole truth rather than a summary.

## 4. Build order — each phase lands green and is independently useful

1. **The fence + the table. Nothing authors, nothing arms — BUILT 2026-08-07.**
   - Close the generic paths (§5.5): the OS-automation directories join the
     un-trustable set at grant time, `denylisted_roots` grows them so a command
     *naming* one is refused pre-gate, the arming binaries (`launchctl`,
     `crontab`, `at`, `batch`) are refused as a command's first token, and the
     seatbelt's deny-write list grows the same directories — write-deny only,
     emitted after every allow like the data-dir denies (reading a plist is
     harmless and the harness may legitimately inspect one).
   - The `automations` table: id, name, label slug, schedule (closed vocabulary,
     §5.4), command, created/updated stamps, `created_in_mode` (display-only
     provenance, as everywhere). **Snapshot-CAPTURED** — it is reversible
     config; `test_capture_scope_covers_every_schema_table` forces the decision
     to be explicit either way. A new slug in `snapshot_manager.REASONS`.
   - `automation.list` / `automation.remove` RPC (list and remove answer in
     every profile — the artifact rule: hiding saved config on a profile switch
     is the failure the 2026-08-06 decision reversed, and a tightening is never
     trapped). No add surface yet; the table stays empty except by hand.
   - Independently useful because the fence corrects a live gap whether or not
     the rest of the step ever lands.

   **What shipped, and the six decisions taken while building** (each stated at
   the code as well; tests named here are the mutation-proven anchors):

   - **The fence, all three consumers.** `policy.OS_AUTOMATION_DIRS` (eleven
     entries — launchd's four, cron's five, systemd's two; `/etc/crontab` is a
     FILE and rides the directory logic unchanged, since every comparison is
     "equal to or under"). Consumed by the trust floor (`workspace_trust_allows`,
     both directions, grant AND authorize time — so a pre-fence trust row over an
     automation dir stopped confining anything the moment this landed, and no
     migration was needed), by `denylisted_roots`, and by the seatbelt
     (`exec.rs`, which derives its OWN copy of the list rather than receive it
     over IPC — the floor must not depend on the core's honesty). The two copies
     are pinned entry-for-entry, order included, by
     `test_g2_the_fence_list_is_in_lockstep_with_the_shell`, which reads the Rust
     source.
   - **The denylist treats automation roots as INSIDE-only.** CONTAINS exists
     because naming a folder that HOLDS the recovery floor destroys it; nothing
     about naming `~/Library` arms anything, and asking CONTAINS here would
     refuse `rm -rf ~/*` on every platform the kernel does not confine — the
     false positive that gets a guard switched off. Cost accepted the other way:
     `cat ~/Library/LaunchAgents/x.plist` is refused, because this layer cannot
     tell read from write; the seatbelt, which can, denies only writes.
   - **The arming-binary refusal is segment-aware and answered first.**
     `command_arms_automation` matches the FIRST word of each shell segment
     (basename, case-folded, dequoted), so `cd /tmp && crontab -` is refused and
     `man crontab` is not; redirects are not segment starts (`echo x > ./out/at`
     is not running `at`). It is answered before path offences so the refusal
     sentence talks about scheduling, not folders. Wrappers (`sudo crontab`,
     `env X=1 crontab`) are conceded — backstop against the obvious, stated as
     such; the trust floor is what closes the path that needs no shell.
   - **The shell extends the collision-drop, not just the deny ordering.**
     Ordering alone holds only for direct parents (`~/Library`) and fails where
     an intermediate directory exists (`/var/spool`'s rename hole) — so a trusted
     root that IS, sits inside, or CONTAINS an automation dir is dropped through
     the same predicate the data dirs use, and the write-denies are the second
     layer. A boundary that holds only when you count path components cannot be
     checked by reading it.
   - **The trust refusal now names its true reason.** One sentence covered every
     floor failure, and the fence made it false for the new group — picking
     `~/Library/LaunchAgents` claimed the folder "holds Addison's own memory".
     `policy.trust_refusal` is the same single loop with the group reported
     (`workspace_trust_allows` is its `is None`); the grant RPC answers the
     fence's own sentence for an automation dir, the memory sentence otherwise,
     and PROTECTED wins on a path that offends both (`~`), so no
     previously-refused folder changed its wording. Frozen copy pinned on both
     sides, core and webview.
   - **The table has no armed column, structurally.** §5.6 as specified — plus
     the projection discipline the build added: `schedule_json` reaches the wire
     only as the closed integer fields of its declared kind
     (`automations.schedule_fields`), so a hand-edited or restored row cannot
     push prose or foreign keys onto a surface, and one malformed row costs
     itself, never the list.

   Tests: `tests/test_automations.py` (the surface, capture, restore, and the
   structural can't-reach-a-process pins), additions to
   `tests/test_workspace_trust.py`, `tests/test_step_5_5_containment.py` and
   `tests/test_g2_no_self_trigger.py` (the fence, both halves, tied to the
   floor), and `exec.rs`'s four profile tests. Every guard was mutation-proven
   by all four builders — the coordinator's pass re-verified the fence's and
   refusal's mutations independently.
2. **Authoring. Drafts exist; nothing reaches the OS — BUILT 2026-08-07.**
   - `create_automation` — an ordinary registered tool, dev-only, that writes
     a row. At the door, the same refusals a command faces at dispatch: the
     denylist (Addison must not *author* what it would refuse to run), and a
     secret-shape check on the stored text (the mcp-phase-1 precedent: this
     table is captured, so anything in it is copied into every later snapshot
     payload and plaintext sidecar).
   - Plist text and the plain-words schedule are **pure functions** of the row,
     testable byte-for-byte. The Settings section (mcp pattern) lists drafts.
   - Removal stays the phase-1 RPC. Every row is honest about state: "not armed
     — Addison can write this for the OS to run, once you arm it".

   **What shipped, and the five decisions taken while building:**

   - **The plist preview is CHAT-ONLY, and structurally so.** The sketch above
     originally said "previewed in chat and on the surface", and building it
     showed the two halves contradict §5.8: a preview on the Settings surface
     means the built document crossing IPC, and a payload that carries a plist
     is a payload that normalises the shell taking one. So the preview lives in
     the authoring tool's own result text (where the person is deciding), the
     surface shows the name, the plain-words sentence and the exact command
     (the row's truth, not the document), and a source-level test pins that
     `rpc/automations.py` cannot even import `plist_text`.
   - **Registered `open_only`, not `dev_only` — deliberately.** Identical
     visibility (absent from SAFE, refused at dispatch outside OPEN), but
     `dev_only` also waives the undo-at-registration check, and this tool is
     MEDIUM with a REAL `undo()` (delete the row it created). `write_project_file`'s
     registration shape, for `write_project_file`'s reason: dropping `undo()`
     must fail registration, not register silently.
   - **Non-destructive, so no card in OPEN — and that is the honest tier.** A
     draft can run nothing; the ceremony belongs to arming (§5.2). The tool
     declares `command_text`, so a forbidden command is refused at every
     dispatch site ABOVE the gate — including the case where the automation's
     own command is `crontab`, which is refused as arming: a scheduled job that
     arms another scheduled job is the ceremony being walked around.
   - **The label is ASCII-folded** (`Zálohování` → `zalohovani`): it becomes a
     filename on a filesystem that folds Unicode, so two rows distinct to
     SQLite's UNIQUE could collide as one plist. Conceded and stated: a name
     with no Latin letters folds to nothing and is refused with the plain
     "give it letters or numbers" sentence — the honest v1 answer.
   - **The scheduling-language prompt gate got a caged exemption, not a hole.**
     `primary.txt`'s "the app cannot schedule anything" SURVIVED phase 2 —
     a draft nothing can run keeps it true — but the capability-claims test
     fires on the word, so `create_automation` is exempted in the OPEN view
     only, with three guards: the id must exist, must be absent from SAFE, and
     its description must still state its own limit. Phase 3 is the commit
     where the sentence genuinely changes (§7).

   Two copy lines now state the not-armed truth — the tool's answer ("arming
   doesn't exist yet") and the surface row ("once you arm it") — and **both flip
   in phase 3's commit**; they are registered in §7 below.

   Tests: `tests/test_create_automation.py` (the door, the registration shape,
   the SAFE boundary, undo, 28 mutations), additions to
   `tests/test_automations.py` (the wire sentence, the plist pin), the
   `automation.list` fixture joining the generated-parity machinery, and
   `shell/src/__tests__/automations.test.tsx` (the section, the parser, the
   profile gate — 20 tests).
3. **The keyword gate + arming. The step's claim becomes true — BUILT 2026-08-07.**
   - The shell surface: `automation.install {label, command, schedule}` /
     `automation.remove {label}` / `automation.status {label}` — the shell
     validates the `com.addison.auto.` prefix, builds the XML itself, writes
     only `~/Library/LaunchAgents/<label>.plist`, runs `launchctl bootstrap` /
     `bootout` for the user domain, and refuses everything else. `RunAtLoad` is
     never set (§5.7).
   - The nonce machinery in the gate and the card (§3), as a new card kind the
     existing `permission.requestGrant` / `permission.respond` round-trip
     carries.
   - `arm_automation` (HIGH, destructive, **real `undo()` = disarm** — the rare
     non-LOW tool whose undo is honest) and `disarm_automation` (a tightening:
     one ordinary card in OPEN, no nonce — never trapped; no `undo()`, because
     its undo would be re-arming without the ceremony, so re-arming is always a
     fresh nonce). Both `dev_only`; both audited on every outcome; arming from
     a routine step or a widget is refused with a plain sentence (§5.10).
   - macOS only, and honest about it: elsewhere the tool answers with one plain
     sentence, the same temperament as the seatbelt's non-mac disclosure.
   - This phase flips the documents in §7 and closes the KNOWN-GAPS question.
4. **State honesty + the Simple profile — BUILT 2026-08-07. STEP 8 COMPLETE.**
   - Armed-ness is reconciled on demand from `automation.status` when the
     surface loads — the OS owns that truth (§5.6); nothing polls, nothing
     checks at startup (the mcp temperament: no action the person did not just
     cause). After a restore or a reinstall the surface says what is actually
     true rather than what the row remembers.
   - **Owed from phase 2:** the Automations section self-fetches
     (`RoutineLibrary`-style), so a G3 restore does not re-read its list while
     Settings is open — every other captured table is re-read by `App.tsx`'s
     `onRestored` closure. The hook + `onRestored` entry land here, where the
     same hook also carries the armed-ness reconciliation.
   - Simple lists automations as **disabled rows that say why** — the artifact
     rule. Every automation is dev-made by construction, so the treatment is
     uniform and needs no per-row predicate; note explicitly that this must
     *not* be implemented by reading `created_in_mode` (the routines gap in
     KNOWN-GAPS was the cautionary entry — closed 2026-08-08, by asking the
     routine what it needs).
   - The Developer review surface (`phase-3-review-surface-plan.md`) records
     itself unblocked.

   **What shipped, and the four decisions taken while building:**

   - **The marker is decided from what an automation IS, not from its stamp.** The
     caller passes a literal `True` to `_unavailable_marker`, because every
     automation's payload is a shell command and there is no such thing as one
     Simple could arm. That uniformity is the safety property: with no per-row
     question, there is no `created_in_mode` to be tempted into reading — which was
     exactly the bug the routines half carried until it was closed on 2026-08-08
     ([KNOWN-GAPS.md](KNOWN-GAPS.md)). A test scans this module's branches for the
     stamp, so the temptation cannot return quietly.
   - **Simple keeps Remove, and only Remove.** Arm and Disarm are the capability
     and both are dev-only, so offering them could only produce a refusal. Remove
     stays because removal is a tightening a profile switch must never trap — and
     because phase 4's own `_disarm_before_forgetting` makes it the one way a
     Simple person can stop a job their computer is running.
   - **The command text is not printed in Simple**, matching the command widget's
     treatment in the rail. In Developer the command is on screen because the code
     ceremony exists to make somebody read it; on a surface that cannot arm, that
     reason is absent and the text is only a developer affordance.
   - **A restore re-reads the ROWS and does not re-ask the OS.** A restore can put
     a row back and can never arm or disarm anything, so the cached armed set stays
     true; re-asking would be a check nobody caused (§5.6's temperament). The OS ask
     lives in the SECTION's effect rather than the hook's mount, so "asked when the
     surface loads" does not quietly become "asked every time Addison opens".

## 5. Decisions

1. **Nonce, not prefix — ANSWERED 2026-08-07 (owner).** §1 and §3. The concrete
   form (alphabet, length, compare, attempt bound) is engineering detail settled
   by this plan, not a second owner decision.
2. **The nonce gates ARMING, and v1 gates nothing else with it.** A powerful
   one-shot command already meets a per-invocation card with the exact text on
   it, runs inside the seatbelt, and lands on the snapshot floor. An **armed**
   automation is the qualitative jump: it recurs, it outlives the session, it
   runs when Addison is closed, and it runs unconfined (§5.7). That jump is
   what earns the strongest consent in the app; spending the ceremony on
   everything would make it the thing people learn to type through. If a later
   step wants the nonce on more actions, the machinery is general — the card
   kind carries a preview and a code, not anything automation-specific.
3. **Dev-only for v1.** The payload of an automation is a shell command; SAFE
   invariant 1 has no place for one, so authoring and arming are OPEN-only —
   `dev_only` registrations, refused at dispatch outside OPEN, absent from
   `visible_tools(SAFE)`. Simple's treatment of existing rows is phase 4's
   disabled-with-reason listing, never hiding.
4. **launchd user agents, and nothing else.** One mechanism, on the platform
   the seatbelt already commits to. No LaunchDaemons ever — that is root's
   domain and no phase may touch it. No cron — on macOS it is a legacy shim,
   and a second mechanism is a second set of edge cases to hold; the step-5.5
   lesson is that one mutation-proven boundary beats two half-owned ones. The
   schedule vocabulary is CLOSED (§5.4a): `interval` (every N minutes/hours)
   and `calendar` (a time of day, optionally a weekday) — both renderable in
   one plain sentence, both mapping 1:1 onto `StartInterval` /
   `StartCalendarInterval`. Drafts exist on every platform; arming is macOS
   only and says so.
5. **The fence, and why it is phase 1 rather than a footnote.** §2 names the
   open path. The directories: `~/Library/LaunchAgents`,
   `~/Library/LaunchDaemons`, `/Library/LaunchAgents`, `/Library/LaunchDaemons`,
   `/etc/cron.d` and friends, `/var/at`. Three consumers, one list
   (`policy.py`, beside `_CREDENTIAL_DIRS`): trust-grant refusal, the pre-gate
   denylist, and the seatbelt's write-denies. A consequence worth writing down:
   `~/Library` can no longer be trusted as a workspace, because it contains an
   automation directory — the same both-directions rule the data dir already
   imposes on `~`. The arming-binary token refusal is a BACKSTOP AGAINST THE
   OBVIOUS in the denylist's own honest scope, not a parser.
6. **Armed truth lives in the OS; the row is the record; a restore never
   arms.** G3 restores configuration in one action, and a nonce ceremony cannot
   hide inside one action — so a restore that resurrects a row containing an
   armed-looking state must not (and structurally cannot) write a plist. The
   row therefore never stores "armed" as authoritative state: the surface asks
   `automation.status` when it loads, and what launchd says is what the person
   sees. A restore, a reinstall, or somebody deleting the plist by hand all
   converge on the same honest answer without a special case.
7. **An armed job runs unconfined, and `RunAtLoad` is never set.** The seatbelt
   confines *Addison's* commands; an armed job is the *person's* automation,
   consented to with the strongest ceremony the app has, run by the OS with
   Addison possibly not even installed any more. Wrapping the job in a seatbelt
   profile would freeze a snapshot of trust that goes stale the moment the
   person trusts another folder — a stale profile is a lie with a safety label.
   The card says "outside Addison's sandbox" instead, which is true forever.
   `RunAtLoad` stays unset so arming never causes an immediate run: the first
   execution happens at the OS's own schedule, which keeps "Addison never
   triggers itself" clean even at the moment of installation.
8. **The shell contract is typed fields, never markup.** §2. The shell builds
   the plist, owns the prefix, and touches nothing outside its one directory.
   The IPC surface cannot express "write this XML somewhere".
9. **No Custom-panel dial in v1.** SAFETY.md's rule is that the panel grows a
   guard only as the capability lands; landing the capability does not oblige
   the dial in the same step, and what a *loosened* nonce would even mean (a
   plain card? a session grant?) is an owner conversation that has not
   happened. The nonce ships non-tunable. If a dial ever lands, weakening it
   mints the G4 anchor like the other guards.
10. **No arming from routines or widgets in v1.** The ceremony belongs where
    the person is present and reading — the live conversation or the
    Automations surface. A stored, replayable spec that can raise a nonce card
    mid-run invites answering it on autopilot, which is the reflex §3 exists to
    break. Refused with a plain sentence at dispatch, like `run_command`'s
    shapes are elsewhere. (SAFE-3 note: this is a *narrowing* of what a routine
    may do relative to live chat, which is the permitted direction.)

## 6. What this step does NOT include

- No Addison-side scheduler, timer, watcher or callback — in any phase, ever.
  That is G2, and it is why "the OS runs it" appears in every sentence.
- No LaunchDaemons, no root, no `sudo`.
- No cron authoring (§5.4), no Windows/Linux arming in v1.
- No keyword on anything other than arming (§5.2), and no Custom dial (§5.9).
- No step-editing UI for automations — the same deliberate absence routines
  have; a draft is replaced by asking for a new one.
- No import/export or sharing of automations (the routine-sharing deferral
  covers the shape).

## 7. What flipped when phase 3 landed (2026-08-07)

Registered before the fact as a checklist, and kept afterwards as the record —
because the value was never the list, it was that a landing diff had one at all.
**Every item below is done**, in the commit that made arming real:

- `CLAUDE.md` — G2's parenthetical, and the step-8 status line (phases 1–3).
- [SAFETY.md](SAFETY.md) — G2's "designed, and **not built**" clause is gone; the
  floor now states the three reasons arming does not touch it (the OS runs the
  job; `RunAtLoad` is never set so arming causes no run; the ceremony is a
  keystroke Addison cannot supply). The §Custom note says the gate shipped and is
  deliberately not tunable (§5.9). The `!run` sketch went in the phase-2 sweep.
- [flows.md](flows.md) — flow 12's *"Not built (step 8)"* paragraph.
- [KNOWN-GAPS.md](KNOWN-GAPS.md) — the keyword-syntax question was already
  answered; its "not built" clause is now the phase-4 remainder.
- `ROADMAP.md` — item 8's status, and what phase 3 shipped in plain words.
- `docs/architecture.md` — the Rust module list gains `automation.rs` (a
  test-enforced closed set), described as the only writer of
  `~/Library/LaunchAgents`.
- `docs/data-model.md` — the `automations` bullet: `arm_automation` is what hands
  a row to launchd, and the table still has no armed column.
- `docs/addison-engineering-spec.md` and `docs/addison-design-doc.md` — the
  G2/keyword sentences each carries, and the design doc's Custom-panel note.
- **`create_automation.NOT_ARMED_LINE`** — it said "arming doesn't exist yet";
  it now says what the person does next and that a code will be asked for.
  (`SettingsPage`'s "once you arm it" half flipped with it.)
- **`primary.txt`'s "the app cannot schedule anything"** and the
  `_AUTHORS_A_SCHEDULE_BUT_RUNS_NOTHING` exemption in
  `tests/test_prompt_capability_claims.py` — the sentence changed rather than the
  exemption growing, exactly as the phase-2 entry said it must.
- **`tests/doc_claims.py`** — the phase-2 row
  (`AUTOMATION_AUTHORING_BUILT_ARMING_NOT`) had its own second half falsified, so
  the row was REPLACED by `automation-arming-built` rather than flipped. It named
  eight stale lines across six files on its first run, which is the whole argument
  for registering a fact in the commit that makes it true.

New load-bearing facts this step created now have their rows: *arming exists and
needs a typed code* is registered above. **A restore never arms** is structural
(no armed column) and pinned by name in `tests/test_automations.py`; **the shell
writes only its own prefix** is pinned in `automation.rs`'s own tests plus the
cross-language plist lockstep in `tests/test_automations.py`.
