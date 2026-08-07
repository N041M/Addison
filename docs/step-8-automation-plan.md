# Step 8 — OS-run automation and the keyword gate

**Status: NOTHING IS BUILT.** This plan turns the recorded decisions into a build
order and settles the engineering decisions those left open. The principle is the
scope amendment's §9 sentence, unchanged since 2026-07-20:

> **Addison authors; the OS runs; Addison never triggers itself. Powerful/armed
> actions require a user-typed keyword.**

`ROADMAP.md` owns scheduling. G2's full text lives in [SAFETY.md](SAFETY.md);
this step is the one that turns its "designed, **not built**" clause into code,
and §7 below names every document line that flips when it does.

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

1. **The fence + the table. Nothing authors, nothing arms.**
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
2. **Authoring. Drafts exist; nothing reaches the OS.**
   - `create_automation` — an ordinary registered tool, `dev_only`, that writes
     a row. At the door, the same refusals a command faces at dispatch: the
     denylist (Addison must not *author* what it would refuse to run), and a
     secret-shape check on the stored text (the mcp-phase-1 precedent: this
     table is captured, so anything in it is copied into every later snapshot
     payload and plaintext sidecar).
   - Plist text and the plain-words schedule are **pure functions** of the row,
     testable byte-for-byte, previewed in chat and on the surface. The Settings
     section (mcp pattern) lists drafts with their previews.
   - Removal stays the phase-1 RPC. Every row in every profile is honest about
     state: "not armed — Addison can write this for the OS to run, once you arm
     it".
3. **The keyword gate + arming. The step's claim becomes true.**
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
4. **State honesty + the Simple profile.**
   - Armed-ness is reconciled on demand from `automation.status` when the
     surface loads — the OS owns that truth (§5.6); nothing polls, nothing
     checks at startup (the mcp temperament: no action the person did not just
     cause). After a restore or a reinstall the surface says what is actually
     true rather than what the row remembers.
   - Simple lists automations as **disabled rows that say why** — the artifact
     rule. Every automation is dev-made by construction, so the treatment is
     uniform and needs no per-row predicate; note explicitly that this must
     *not* be implemented by reading `created_in_mode` (the routines gap in
     KNOWN-GAPS is the cautionary entry).
   - The Developer review surface (`phase-3-review-surface-plan.md`) records
     itself unblocked.

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

## 7. What flips when phase 3 lands

Registered here so the landing diff is a checklist rather than an archaeology
dig. Each is one sentence today and each currently says "designed, not built"
in some spelling:

- `CLAUDE.md` — G2's parenthetical "(designed, **not built**)".
- [SAFETY.md](SAFETY.md) — G2's "designed, and **not built**: it is Phase-2
  step 8 and there is no keyword-gate code in the tree", and the §Custom note
  "(the keyword gate is Phase-2 step 8 and does not exist)". The `!run` example
  in G2's text is the superseded sketch and goes with it.
- [flows.md](flows.md) — flow 12's *"Not built (step 8)"* paragraph, which
  still describes the prefix sketch.
- [KNOWN-GAPS.md](KNOWN-GAPS.md) — the "Keyword-gate syntax (blocks step 8)"
  open question closes (the syntax half is answered by §1; the scope half by
  §5.2).
- `ROADMAP.md` — item 8 moves to Built, and the review-surface plan's
  "blocked on step 8 alone" clause unblocks.
- `HANDOFF.md` — rewritten as always.

New load-bearing facts this step creates (the nonce is per-automation and
single-use; a restore never arms; the shell writes only its own prefix) get
rows in `tests/doc_claims.py` in the phase that makes each true.
