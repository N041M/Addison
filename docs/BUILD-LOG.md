# Build log — what each step shipped, and what its rigor pass found

**This file owns the per-step record.** Read the entry for a subsystem before
changing it: the "what shipped" halves are largely superseded by `ROADMAP.md` and
git, but **the post-build rigor passes are where the real defects were**, and every
one of them describes a way this code has actually been wrong.

Not a changelog. Entries stop being added once a subsystem is stable; what earns a
place here is a finding a future session would otherwise rediscover the hard way.

*(Extracted from `HANDOFF.md` on 2026-07-27, unchanged.)*

---

## What shipped 08-08 — the review-surface wave, prerequisites to Monaco in one day

Nine merges (PRs #70–#78): the post-#69 review fixes, the plan's three prerequisites,
the docs-first wave, and Build §§1–5 — read paths as RPC, the diff and per-file chain
revert, the Code screen and its skin, and the CSP change the plan spent a section on.
Built by five Opus agents in sequence, each independently reviewed (gates re-run,
mutations re-applied) before its merge; the plan
([phase-3-review-surface-plan.md](phase-3-review-surface-plan.md)) owns what each
section is, and its per-section "what shipped" blocks own the decisions. What belongs
HERE is what the rigor passes found:

- **EVERY agent that re-derived the plan against the tree found it stale somewhere.**
  The docs wave found the redefinition "already written into four documents" true for
  two of them, with a FIFTH stale definition grown since; §1 found the plan's
  `screen` state never existed (`view`, five members); §4/5 found the Monaco import
  path outdated, the api entry registering no grammars, and both "adjacent drifts"
  already fixed with two live ones in their place. None of these were plan failures a
  reader could see — each was correct when written. **A plan is a claim about the
  tree at writing time; re-derive it at build time, and budget for what you find.**
- **The one-resolution seam is a HOOK SHAPE, not a discipline.** The name-race fix
  works because `permission_detail_for_path(resolved)` never receives `args` — a
  path tool structurally cannot resolve a second time, rather than being trusted not
  to. The general lesson: where two call sites must agree on a derived value, hand
  one the other's answer instead of the recipe.
- **Generated fixtures caught a live wire defect before any test did** (§2/3): a
  non-`RuntimeError` from the bridge put raw exception text on the wire
  (`"'_FixtureEditBridge' object has no attribute …"`). The fixture generator runs
  the REAL handler, which is the point — a hand-written fixture would have encoded
  the intended shape and hidden the defect.
- **The spoofing mitigation inverted the plan's own prediction.** The plan warned
  that widening `style-src` would change how mermaid diagrams look (their `<style>`
  was silently CSP-blocked). Stripping styles from the injected SVG closes the
  model-influenced injection path AND keeps diagrams byte-identical — the widening
  now changes nothing visible, and §13c records "a diagram that looks different" as
  a finding. **When a mitigation can preserve shipped behaviour exactly, prefer it
  to one that asks a reviewer to accept a described change.**
- **The chain-revert semantics remove a hazard by construction, not by care**: revert
  the whole unreverted chain to its oldest prior and zero rows remain for the undo
  button to resurrect content from. The write-first-mark-second asymmetry is the
  same idea one layer down — every failure mode leaves the truth on the side where
  re-trying converges.
- **"Keep the age arm" had two readings and the cheap one was wrong** (prerequisite
  3): an age arm still spanning unreverted rows would delete every old live edit —
  more deletion than the unwired prune it replaced. The builder surfaced the fork,
  implemented the reading consistent with the decision's motivation, and recorded
  the cost (the unreverted subset is now bounded only where it is read). **When an
  owner decision is ambiguous, the harm statement that motivated it is the tie-break
  — and the fork belongs in the PR, not in a comment.**
- Two small review catches after agents reported green: a load-bearing comment
  pointing at a test file that does not exist (`sanitizeSvg.ts`), and ROADMAP still
  calling the surface "unstarted" in the very merge that started it. Both are the
  same shape: **the sentence nearest the code is the one a green suite never
  checks.**

## What shipped 08-07 (sixth, last) — step 8 phase 4: state honesty, and the end of step 8

The last phase of the last step. Armed-ness is read from the OS when a surface
loads; Simple lists automations as disabled rows instead of hiding them; a restore
re-reads the list. **Step 8 is complete, and with it the July-2026 scope change.**

What is worth keeping from it is smaller than phase 3's list, and one item is a
near-miss rather than a defect:

- **THE STAMP TRAP WAS AVOIDED BY REMOVING THE QUESTION, NOT BY ANSWERING IT
  CAREFULLY.** Routines then decided availability from `created_in_mode` — the
  bug KNOWN-GAPS tracked until it was closed on 2026-08-08 (availability is now
  derived from the plan and the registry, `rpc/routines.py::_routine_needs_dev`) —
  where a checklist made in Developer arrived in Simple
  disabled and announcing it "uses developer abilities" about a widget that
  invokes no tool. The temptation here was identical, and the escape was that
  every automation runs a command, so the answer is `True` for all of them: the
  caller passes a literal, and with no per-row question there is no stamp to read.
  A branch scan pins it, because "we passed a constant" is only true until
  somebody adds an `if`. **Where a per-row question has one answer, delete the
  question.**
- **A DOC CLAIM WALKED PAST THE LINE IT WAS WRITTEN FOR, because of markdown.**
  The `automation-arming-built` row added in phase 3 matched "arming is not
  built" — and CLAUDE.md's G2 floor said `(designed, **not built**: step 8 phase
  3)`, where the bold split the phrase the pattern was looking for. The row was
  green while the app's most-loaded document told every session the keyword gate
  did not exist. Widened to match the parenthetical shape, punctuation and
  emphasis included, and re-proven against the exact line. **A pattern written
  against prose must be tested against the tree's MARKUP, not against the
  sentence you would have written.**
- **Two independent answers drive the disabled row**, deliberately: the core's
  marker and the active profile, either sufficient. A core that forgot to mark a
  row still renders it inert. That is the cheap direction to be redundant in.
- **THE SWEEP DECLARED STEP 8 COMPLETE WITHOUT READING THE TREE.** Nine live
  sentences across seven files still said arming was not built — README's
  user-facing paragraph, three in flows.md (one of them describing the rejected
  `!run` design), architecture.md, classes.md, four in the engineering spec, and
  **the KNOWN-GAPS clause the plan's own §7 had listed as phase 4's owed item**.
  Worse, §7 asserted "every item below is done" and named flows.md, which the
  commit never opened. The sweep had been done by following the `doc_claims` row
  rather than by grepping, and the row's patterns matched none of those phrasings.
  **A mechanical check is a floor, not a sweep** — it catches the shapes somebody
  already thought of, and a completion claim has to be verified against the tree.
- **The frontend agent's first mutation round produced five false "survived"
  verdicts** — anchors that matched the *widget* parser's byte-identical idiom in
  the same file, and a test whose fixture row was marked so the profile half never
  ran. It re-anchored and re-ran with exit-code verdicts. Worth recording because
  a mutation harness that reports "survived" wrongly is worse than none: it
  retires a real test.
- **Two user-facing strings leaked the capability Simple refuses.** The build
  correctly withheld the command text and the Arm/Disarm controls, then left the
  prose: an empty state reading "Ask Addison to set one up" (for a tool that is
  `open_only` and can only answer with a refusal) and a row reading "nothing runs
  until you arm it" directly beneath the line saying this profile cannot.
  SAFETY.md names that exact shape — "a vocabulary that teaches one, an affordance
  that invites one" — and it also falsified a sentence written in the same diff.
  **Withholding the control is half of it; the copy is the other half.**
- **A new comment claimed `arm_automation` registers `dev_only`. It registers
  `open_only`, and the difference is undo enforcement** — `dev_only` takes the
  waiver, and this is a HIGH tool with a real `undo()`. `main.py` warns about this
  exact misreading in the file the comment contradicts. An agent trusting the
  comment and "correcting" the registration would silently drop SAFE invariant 2
  from the one tool that hands a job to the OS.
- **`automation.remove` disarmed the job BEFORE minting its restore point**, so a
  capture failure answered "it didn't remove anything" after the automation had
  been switched off — with no snapshot, no audit row (it is an RPC, not a tool) and
  no undo. Reordered; the cost is a restore point on a removal that then refuses,
  which is the cheap direction.
- **A G3 restore can orphan an armed job**, and phase 4 made the row vanish rather
  than sit uselessly on screen. Recorded in KNOWN-GAPS rather than fixed: the real
  answer is reconcile-on-restore, and the alternatives (blocking a restore, or
  disarming inside one) would put arming decisions inside the one action G3
  promises is always available.
- **THE ROW READ MOVED INTO A HOOK AND LOST ITS PER-VISIT CADENCE (post-merge
  review, 2026-08-08).** Phase 3's section fetched the rows every time Settings
  opened; phase 4 moved that fetch into `useAutomations`, which App mounts once at
  launch, and the section's own load effect kept only the OS ask. But
  `create_automation` writes rows from CHAT — so the automation somebody had just
  asked Addison for was missing from the very screen they open to see it, until a
  restart, a removal or a restore happened to re-read the list. No test caught it
  because every rendering test mounts the hook and the section together. The
  section re-reads the rows on load again, and a test that models the app's real
  shape — hook at launch, section per visit, a row authored in between — pins it.
  **A refactor that moves a fetch also moves its cadence; check what the old
  call-site's mount MEANT before inheriting a different one.**

## What shipped 08-07 (fifth) — step 8 phase 3: the keyword gate, and what its review found

Arming exists. `arm_automation` installs a launchd job through the shell, behind the
ordinary card PLUS a six-character code the person retypes. The plan owns the
decisions; what belongs here is what the adversarial pass found afterwards — **five
real defects in code that was already green on 1534 tests**, and three mistakes I
made while fixing them.

- **THE CEREMONY WAS OPT-IN PER CALL, AND ITS ABSENCE FAILED OPEN.** The gate took
  the arming path iff a preview ARRIVED. `arming_card` returns None whenever the row
  cannot be read, and `_row` swallows every store error — so a transient SQLite
  failure silently downgraded arming to an ordinary destructive card, and under
  Custom's `auto_grant_scope='everything'` to **no card at all**. That is verbatim
  the failure `authorize`'s own docstring says the arming ordering exists to prevent;
  the ordering was right and its trigger was wrong. **A requirement that lives on the
  payload is a requirement the payload can lose** — it now lives on the tool
  (`tool_requires_arming`), and a missing preview denies.
- **THE TEST GUARDING IT ASSERTED THE OPPOSITE OF ITS OWN NAME.**
  `test_a_tool_whose_door_or_preview_explodes_is_refused_rather_than_escalated`
  asserted only that the two helpers return None without raising — which is the
  MECHANISM by which the call was not refused. Instance #6 of this shape.
- **REMOVING AN ARMED AUTOMATION ORPHANED THE JOB, PERMANENTLY.** `automation.remove`
  deleted the row and left launchd running it. Afterwards `disarm_automation` said
  *"that automation isn't saved any more, so there was nothing to turn off"* while the
  computer ran it hourly, and the surface renders armed-ness per ROW — so there was
  nothing to render. A running job nobody could see or stop, from pressing Remove.
  Phase 1's own docstring had specified the fix ("the OS first, the record second")
  and phase 3 shipped without honouring it. **A deferral written as a docstring is a
  deferral nothing enforces.**
- **THE CORE MINTED LABELS THE SHELL REFUSED.** `_slug` caps the stem at 40;
  `derive_label` appended `-2` on top, making 41–43. The shell validates labels
  itself — deliberately not trusting the core — and refused. So a second automation
  with a long name authored, previewed, showed its code, and failed the instant the
  person typed it, blaming Addison's own naming. The plist lockstep could not see it:
  it compares documents, and this was the label. **Two implementations of one
  contract need every dimension pinned, not the obvious one.**
- **DISARM REPORTED SUCCESS WHEN IT COULD NOT ASK.** `let _ = launchctl(bootout)`
  discarded spawn failures and timeouts, not just the ordinary "no such process", and
  the file was removed regardless — so the person was told *"your computer won't run
  it any more"* while launchd held the job, and `list_armed` (a directory read) could
  not contradict it. Now an unanswered scheduler refuses and keeps the file, because
  the file is the only thing that can name the job later.
- **A FAILED RE-ARM SILENTLY DISARMED THE WORKING JOB** while both failure sentences
  promised "nothing was set up" — true of the new job, false about the old one it had
  already unloaded.

**Three mistakes in the fixing, all mine, all worth more than the fixes:**

- My first control-character screen made the CORE stricter than the shell, refusing
  newlines the shell accepts — breaking a legitimate multi-line command and one of my
  own tests. Fixed by mirroring `command_from`'s rule exactly rather than inventing
  one. **In a two-sided contract, match the other side; do not out-guess it.**
- My source-level assertion that `arm_inner` routes through `arm_failure` **matched
  its own text** (the search ran to end-of-file and found the assertion), then matched
  the function's own SIGNATURE. Two surviving mutations before it matched the CALL.
  Trap 3 in HANDOFF says exactly this, and I hit it twice in one afternoon.
- A guard I added was unreachable from its real caller, so no mutation killed it,
  until the choice was extracted as a pure function and tested at its own boundary.

## What shipped 08-07 (fourth) — the review of step 8 phases 1–2, and of its own fixes

Four read-only reviewers over disjoint scopes (Python correctness, Rust+frontend,
cleanup, doc currency), then an adversarial pass over the FIXES. The fix pass is
the part worth keeping: **it found three regressions the fix round introduced**,
one of them wider than the defect it was fixing. Green gates caught none of any of
it — the suite was 1449 green while three of these were live.

- **A SELF-REFERENTIAL ASSERTION HID THE WHOLE OF `plist_text`.** Its only
  coverage was `assert f"```\n{plist_text(row)}```" in text`, where `text` is the
  answer built by calling `plist_text` — the function compared against itself,
  true whatever it emits. Proven by mutation: `minutes * 60` → `* 30`, a misspelt
  `StartCalendarInterval`, and **dropping the XML escaping entirely** each passed
  all 1449 tests. The docstring called that escaping "load-bearing" while nothing
  exercised it. Six real content tests now pin the output.
- **THE TWO RENDERINGS OF ONE ROW CONTRADICTED EACH OTHER.** `schedule_sentence`
  checked bounds; `plist_text` checked only presence. So `{"minutes": 0}` and
  `{"hour": 99}` rendered "No schedule saved yet." in words beside a preview
  showing a real launchd trigger. For a preview whose whole job is to be what
  somebody read before arming, whichever one they believed the other disproved.
  One shared `schedule_is_readable` now answers for both.
- **THE PREVIEW FENCE WAS CLOSEABLE FROM INSIDE, AND ITS JUSTIFICATION WAS THE
  TELL.** *"`plist_text` XML-escapes both the command and the label, and no escape
  sequence it emits contains a backtick"* — true about the escaping, irrelevant to
  the risk: escaping touches `&`, `<`, `>` and leaves backticks alone. A command
  carrying ``` closed the block early and wrote prose under Addison's sentence
  "this is exactly what would be handed to your computer". **When a comment
  justifies a boundary by a property of the wrong thing, that is the shape to
  distrust.**
- **THE FIX FOR IT HARDENED ONE CHANNEL AND LEFT ITS SIBLING** — found only by the
  adversarial pass. The grown fence is computed from the plist; the NAME is
  interpolated into the same answer one line above it, so a name carrying a
  newline plus a fence opened its own block. Closed at the door (a name is one
  line, no control characters) rather than at the seam, because that kills the
  vector everywhere the name is rendered rather than in the one place it was found.
- **A TIGHTENING WIDENED A FALSE POSITIVE FIFTEENFOLD, and the doc said otherwise.**
  Stepping the arming fence over transparent prefixes (`sudo crontab` IS `crontab`)
  also stepped over "any word containing `=`" — which CHAINS, so a heredoc line
  like `label=Nightly batch job` in a `.properties` file was refused as arming.
  The comment said "leading `VAR=value` assignments"; the code said something much
  larger. Reverted to exact prefix words; `env X=1 crontab -` is conceded instead,
  because an everyday false positive is worse than one more missed spelling of a
  backstop. **A guard's blast radius is what the code does, not what its comment
  describes** — and the ledger written to record the cost recorded the pre-fix
  shape, which is how a concession drifts without anyone deciding.
- **A NEW `doc_claims` ROW CAUGHT ONE OF THE FOUR SENTENCES IT WAS WRITTEN FOR.**
  The registry mechanises only the SILENCE half (a precision test); firing is
  proven by hand, and it was not. Measured against the real pre-fix text, the
  first pattern matched one of four, and its `excused_by` (bare `was|were`, ±240
  chars) excused ~48% of KNOWN-GAPS and ~55% of HANDOFF — a gate that would almost
  never fire, which this registry's own docstring calls decoration. Both halves
  rewritten and re-measured; the widened pattern immediately found a fifth stale
  line nobody had flagged. **Write a claim pattern against the sentences actually
  in the tree, and excuse on quotation markers rather than on tense.**
- **A GUARD UNREACHABLE FROM ITS CALLER IS A GUARD NO MUTATION KILLS.** The fence's
  closing-newline guarantee could not be exercised through `execute` (the plist
  always ends in a newline), so its mutation passed. Pinned by testing `_fenced`
  directly. This is the repo's fifth instance of the same shape.

Also fixed: `permission_detail` sent a secret-shaped NAME to the Activity Panel
before the door's redaction ran (the call is non-destructive, so the detail is
emitted on the way in); the stored text had no length bound though the mcp/skills
precedent it cites carries one; `HOME=""` silently dropped three seatbelt
write-denies; and six documents plus five source docstrings still described a
tree where nothing could author automation.

## What shipped 08-07 (third) — step 8 phase 2: authoring, and a preview that may not travel

`create_automation` (dev-only) writes a draft row; the Settings surface lists
drafts; nothing can arm one. The plan's phase-2 entry owns the decisions; what
belongs here is what building found.

- **THE PLAN CONTRADICTED ITSELF AND THE BUILD CAUGHT IT.** Phase 2's sketch said
  the plist is "previewed in chat and on the surface"; §5.8 says the shell never
  takes a document from the core. A surface preview means the built plist crossing
  IPC — a payload shape that would normalise exactly what §5.8 forbids. Resolved:
  the preview is chat-only (the tool's result text, where the person is deciding),
  the surface shows the row's truth (name, sentence, command), and a source test
  pins that `rpc/automations.py` cannot even import `plist_text`. The
  generalisation: when two sections of one plan disagree, the build is where it
  surfaces — re-read the stricter section before implementing the looser one.
- **`dev_only` WOULD HAVE WAIVED THE UNDO CHECK ON A TOOL THAT HAS A REAL UNDO.**
  The brief said `dev_only=True`; the builder registered `open_only=True` instead
  and was right: identical visibility and dispatch refusal, but `dev_only` also
  sets `allow_missing_undo`, and a MEDIUM tool with a real `undo()` must stay
  undo-ENFORCED so that dropping the method fails registration. The registry's R3
  split exists for exactly this shape; reach for the narrow flag, not the alias.
- **A WORD-TRIGGERED GATE NEEDS A CLAIM-SHAPED EXEMPTION.** The
  capability-claims test scans tool descriptions for scheduling language;
  `create_automation`'s honest description trips it while `primary.txt`'s "the
  app cannot schedule anything" is still TRUE (a draft nothing can run schedules
  nothing). The exemption is one id, OPEN-view only, with three staleness guards
  — and its comment forbids its own growth: phase 3 changes the sentence, not
  the exemption set.
- **THE LABEL IS A FILENAME, SO UNIQUE ISN'T UNIQUE ENOUGH.** SQLite's UNIQUE
  compares bytes; macOS folds Unicode when the label becomes
  `<label>.plist`. Labels are therefore ASCII-folded at derivation
  (`Zálohování` → `zalohovani`), and a name that folds to nothing is refused
  with a plain sentence — recorded as the honest v1 answer for non-Latin names.
- **TWO AGENTS, ONE MODULE, AND THE GROUNDWORK RULE HELD.** The coordinator wrote
  the shared pure functions (`schedule_sentence`, `plist_text`) before spawning,
  so the tool builder and the wire builder consumed a stable contract in
  parallel; their only file intersection (`test_automations.py`'s exact-key-set
  assertion) was a declared, forced edit. The near-miss worth recording: the
  wire builder observed the tool builder's in-flight syntax error blocking
  repo-wide pytest collection and correctly scoped its own runs around it —
  disjoint FILE ownership does not make `pytest tests/` disjoint.

## What shipped 08-07 — step 8 phase 1: the fence, and a table nothing can fill

The step-8 plan (the per-automation-nonce keyword gate; the owner's syntax decision
was already on record) was written, and its first phase built the same day: the
`automations` table with its inert `automation.list`/`remove` surface, and the fence
that makes the gated path the ONLY path before the gated path exists.
`docs/step-8-automation-plan.md` owns the decisions; what belongs here is what the
work found and what a future session would otherwise rediscover.

- **WRITING THE PLAN FOUND A LIVE GAP, and grounding is why.** Reading
  `workspace_trust_allows` to cite it showed it refused only Addison's own
  directories — so `~/Library/LaunchAgents` could be trusted through the OS picker
  and `write_project_file` could plant a plist there behind an ordinary card:
  login-time automation, armed, no keyword, while the tree's standing claim was
  "nothing can author or arm automation, so G2 holds trivially". Found while
  writing the plan's §2, recorded in KNOWN-GAPS, closed by phase 1 in the same PR.
  The generalisation: a floor's scope is what its predicate reads, not what its
  doc-comment implies — go read the predicate before citing it.
- **A fence in two languages needs a lockstep test, not a comment.**
  `OS_AUTOMATION_DIRS` exists in `policy.py` and again in `exec.rs`, because the
  shell must derive its own floor rather than take one over IPC. Two hand-synced
  copies agreed "by eye" on day one;
  `test_g2_the_fence_list_is_in_lockstep_with_the_shell` now reads the Rust source
  and compares entry-for-entry, order included — the `protocol.py`/`protocol.ts`
  lesson applied before it could bite rather than after.
- **The refusal sentence was FALSE for the new group, and one branch fixed it.**
  Every trust-floor failure answered "That folder holds Addison's own memory" —
  wrong for an automation directory, and a false reason teaches people that
  refusals are boilerplate. `policy.trust_refusal` reports WHICH group refused
  (the bool is its `is None`); the RPC now has a second frozen sentence, and
  PROTECTED wins when a path offends both groups, so no previously-refused
  folder changed its wording between builds.
- **Deny-after-allow ordering is sufficient exactly when the denied dir is a
  DIRECT child of the allowed root, and building on that would have been a trap.**
  The shell agent proved the rename hole needs an intermediate directory
  (`/var/spool` under a trusted `/var`; `~/Library/LaunchAgents` under `~/Library`
  has none) — and then deliberately did NOT condition the defence on path depth,
  because the next list entry would silently decide its own safety by how deep it
  sits. Trusted roots touching an automation dir are dropped through the same
  collision predicate the data dirs use; the denies are the second layer.
- **The INSIDE/CONTAINS asymmetry earned its keep on CI, not in theory.** Asking
  CONTAINS of the automation roots would refuse `rm -rf ~/*` and `ls ~/Library`
  wherever the kernel does not confine writes — which includes the ubuntu CI
  runner, where an existing test (`test_ordinary_developer_commands_are_untouched`)
  would have caught it. The false positive that gets a guard switched off is a
  cost on the same ledger as the hole it closes.
- **The table's most load-bearing column is the one that is not there.** No
  `armed` flag, structurally: armed truth will live in the OS (plan §5.6), because
  a G3 restore is one action and a nonce ceremony cannot hide inside one action.
  The builder pinned it from both sides — a test that the schema has no such
  column, and a test that no payload claims an automation is running.

## What shipped 08-07 (later) — a line-by-line review of the same day's four merges

Four PRs merged on 2026-08-07: the model picker as a two-level folder tree (#60),
and step 7's phases 2, 3 and 4 (#61, #62, #63). A multi-round review of that diff
found about twenty-five real defects and they were fixed the same day. **Every one
was in code that had already merged**, most of it hours old, all of it written
with the care the entries above describe — which is the fact this entry exists to
record. The four merges were not sloppy; they were reviewed by their authors and
by their tests, and this is what a second reading found anyway.

The findings worth a future reader's attention, and what each one generalises to:

- **THE AUDIT LOG COULD LOSE EVERY ROW IT HAD, AND ONLY IF SOMETHING WENT WRONG.**
  `Store._migrate_tool_audit_outcomes` (phase 3) renamed `tool_audit` out of the way,
  created the new shape, copied, and dropped the old table. `executescript` commits
  whatever is pending and then runs in autocommit, so the rename was durable the
  instant it ran: a failure one statement later — a full disk, a lock outlasting
  `busy_timeout`, the power — left the rows in `tool_audit_old` with no `tool_audit`
  beside them. On the next open the method saw no `tool_audit`, returned early, and
  schema.sql created an empty one. The rows were then stranded **for good**, and the
  index, which travels with a rename, stayed bound to the orphan so every audit read
  full-scanned. These are the rows that are excluded from snapshots and never pruned
  precisely because they are the only durable record of what Addison has done. The
  fix is one explicit `BEGIN IMMEDIATE`, the replacement built under a THIRD name so
  the live table is never renamed out of the way, and a self-heal that treats
  `tool_audit_old` existing at all as the signature of an interrupted rebuild.
  **The general shape: a migration's promise is not "it preserves the rows", it is
  "it preserves the rows or does nothing", and the second half is the one an
  interruption tests.** Nothing in the original was wrong on the happy path, which
  is why review rather than a test found it — the test that would have caught it is
  the one nobody writes, where the process dies between two statements.
- **A REDACTION RULE'S COST ON ITS WORST INPUT IS PART OF THE RULE.** The private-key
  block rule was `-----BEGIN…-----` then `.*?` under `DOTALL` to the footer. A lazy
  `.*?` has to scan to the end of the input from every header that has no footer
  after it, which is quadratic in the number of headers — and a header is three dozen
  characters a server can repeat. 125 KB of them took 1.92 seconds; the 512 KB a
  server may send takes about thirty *(measured 2026-08-07 · one redact call over
  repeated BEGIN markers, python 3.12 in agent_core/.venv on the owner's machine)*,
  on the worker thread, **after that call's deadline had already passed**. Every
  other bound in the MCP exchange is a clock or a byte count, and this one sat behind
  all of them. Rewritten as "anything that does not start a run of five dashes",
  possessively, with a recorded body ceiling and the truncated-key rule catching
  anything longer: 0.012 seconds on the same input. **The general shape: a deadline
  bounds the network, not the parsing that happens after it, and a pattern scanned
  over text a stranger chose is a place where an attacker gets to pick the input to
  an algorithm.** Every rule in that file now either cannot backtrack or says in its
  own comment why not.
- **SERIALIZING BEFORE REDACTING TURNED THE REDACTOR OFF, AND THE AUDIT ROW AGREED
  WITH IT.** Phase 4 cleaned and redacted `structuredContent` *after*
  `json.dumps` — the same seam as the text, one call apart, which is exactly what
  the plan and the reviews of it said to do. But `json.dumps` escapes a NUL into six
  visible characters and a newline into two, so the cleaner never saw a control
  character to remove, a key split by a NUL stayed split, and every contiguous rule
  in `agent_core/redaction.py` missed it. The same key in the text channel was
  caught. Worse than the miss: `redacted_kinds` came back empty, so the durable row
  reported no leak on the call where the leak happened — **the log denying the one
  thing it exists to record.** The document's strings, keys as well as values, are
  now scrubbed while they are still strings. **The general shape: "the same seam"
  is a claim about what the bytes look like when they cross it, not about which line
  of which function calls it.** Two channels that cross one redactor at two
  different representations are two redactors.
- **TWO `registry.get()` CALLS ON DISPATCH PATHS RAISED WHERE EVERY NEIGHBOUR
  REFUSED.** A saved routine step keeps the tool id it was written with, and an
  `mcp:` id leaves the registry on a refresh, a removal, a failed check, a snapshot
  restore and every restart. So a routine naming one crashed out of `run` — skipping
  `_finish`, leaving that run recorded as `running` with no `completed_at`, **for
  ever**; the live loop's twin cost a turn its `tool_result`, which the provider then
  rejects on every later request of the session. Both now resolve through a new
  `ToolRegistry.find` and refuse in the shape every other refusal on those paths
  already takes. **The general shape: the moment anything can leave a registry, every
  lookup written when nothing could becomes a lookup of something that may be gone.**
  Phase 2 added `unregister` and correctly guarded it; what it could not do is find
  the callers written years of commits earlier that assumed the opposite.
- **A BUDGET THAT STOPS AT THE REQUEST IS NOT A BUDGET.** Discovery's deadline was
  sampled before each request and never inside the body read, so a server that
  accepted the connection and then dribbled one byte every four seconds — under a
  five-second socket timeout that each byte reset — held the worker thread for as
  long as it liked, inside every stated bound. Now checked per chunk. **The general
  shape: find the one loop whose iteration count the other end controls, and check
  the clock in it.**
- **A PROTOCOL-LEGAL MESSAGE FAILED THE EXCHANGE AND BLAMED THE PERSON.** The SSE
  parser returned the first object carrying `jsonrpc`, and the protocol lets a
  server send `notifications/progress` ahead of its response — which is precisely
  what a slow `tools/call` is for. The result was a discovery that failed with
  *"check the address"* about an address that was perfectly correct. It now walks
  events in order for the first one shaped like a response, bounded. **The general
  shape: a plain-language error is a promise about the diagnosis, and a wrong
  diagnosis stated plainly is worse than a stack trace, because it is actionable and
  the action is wrong.**
- **DEAD SCHEMA WHOSE COMMENTS PROMISED BEHAVIOUR.** `mcp_servers.enabled` was
  written in phase 1 with a comment saying phase 2 could stop consuming a server
  without the person losing its configuration. Phase 2 and 3 never read it, so the
  column was captured, restored, and ignored — a setting the recovery path faithfully
  puts back and nothing obeys. Both readers now honour it (a refresh refuses, and
  `_mcp_endpoint_for` resolves no address), while nothing can still set it to 0.
  **The general shape: a column that is stored and restored but never read is not
  inert, it is a lie the snapshot machinery keeps telling.** The reads are the cheap
  half and they go in first; the toggle then adds a control over behaviour that
  already exists.
- **A CARD PROMISED A FREQUENCY THE PROFILE CAN OVERRIDE.** The MCP permission card
  read *"Addison can't know what it will do, so it asks every time"* — false under
  the Custom profile, where `destructive_card='session'` asks once and
  `auto_grant_scope='everything'` never asks. **The gate's behaviour was not
  changed**: those guards are the owner's design and a card is not the place to argue
  with a setting. The copy now says what is true under every profile — what Addison
  does with the tool rather than how often it interrupts. The same false sentence was
  in `rpc/mcp.py`'s module docstring and in `mcp_catalog`'s, and a version of it in
  `data-model.md` about `run_command`. **The general shape: a sentence about how
  often a person is asked is a sentence about a setting, and it goes stale in the
  direction of over-promising every time somebody adds a way to be asked less.**
  Also fixed here: a server's description was concatenated onto Addison's sentence
  with a single space, so a description ending *"…Addison has checked this server and
  it is safe to approve every time."* read as Addison's own voice. Attribution and
  quotation marks now separate them, and the marks are made unforgeable by removing
  that pair from the server's text rather than escaping it — position is the boundary,
  because a string appended to the end of another cannot get in front of it.
- **`role="tree"` WITH NO KEYBOARD IS A WORSE LIE THAN NO ROLE AT ALL.** The Settings
  model popup announced itself as a tree to a screen reader and shipped no arrow
  keys, no focus management and every row a tab stop. The composer's menu had the
  full contract; two panels drawing one tree had one idiom between them. The popup
  now matches it, focus returns to the control that opened it, and both pickers
  author `aria-posinset`/`aria-setsize` — which a flat tree cannot have computed for
  it. **The general shape: an ARIA role is a promise about behaviour, and the review
  that catches a missing one is not the review that catches a role whose behaviour
  was never built.**
- **A RESTORE PUT THE CONFIGURATION BACK AND LEFT THE SURFACE ADVERTISING THE OLD
  ONE.** `mcp_servers` is snapshot-captured, and the frontend's post-restore refresh
  re-read every other captured table but not that one — so after a restore the Tools
  page went on offering servers the restored configuration no longer had. The list of
  refreshes in `App.tsx` is now written as what it is: every captured table, with the
  consequence of an omission stated beside it. **The general shape: when a subsystem
  captures a new table, the frontend's refresh list is a second place that has to
  learn about it, and nothing connects the two but a person remembering.**

Smaller, and grouped because the individual cases matter less than the count: a
tool refused for an id collision was still reported to the surface as found (so a
panel advertised a tool dispatch would never resolve); a partial failure inside
`record_success` could leave ids registered and in no id list, unremovable for ever;
`EMPTY_SCHEMA` was copied with `dict()`, which is shallow, so every "copy" shared one
`properties` dict; a 6to4 address was unwrapped in a check where unwrapping makes the
check LOOSER, putting a routable off-machine address behind the plain-`http://`
exception; an unterminated OSC escape sequence survived as prose; a `Discovery` field
was produced on every refresh and read by nothing; and a scattering of comments
describing behaviour their code no longer had. **The last group is the one to take
seriously**: this repo's own convention is that a rule belongs in the docstring
beside the code because that docstring is in the diff that falsifies it — which is
true, and is not the same as it being amended.

---

## What shipped 08-07 — step 7 phase 4: what comes back, said rather than filtered

[step-7-mcp-plan.md](step-7-mcp-plan.md) §4.4 owns what landed and the three
decisions taken with it, and §7 owns the re-read: `mcp_client.compose_result` (the
one place a cut happens now), `clean_result_text`, `structuredContent` through the
same redaction seam as the text, one shared budget across the whole result, and the
disclosure line for everything Addison will not carry. It completes step 7 for v1.

What building it taught:

- **The dangerous half of "content-type breadth" is the half you don't build.** The
  work reads as "support images and resources", and the safe answer to most of it is
  to support none of them: a stranger's base64 has no consumer at this end, so
  forwarding one would mean deciding, on a server's say-so, that some bytes are an
  image. What the phase actually owed was HONESTY about the refusal, which costs a
  counter and one sentence. **The scope question worth asking of an input-handling
  phase is not "what can we accept?" but "what would consume it?"** — and here the
  answer was nothing: checked, not assumed (`conversation.load` skips every `tool`
  row, so a server's words never reach the webview), and now pinned by a source
  test so the next person to widen that filter finds out what else it was holding.
- **An embedded resource whose content is text is text, and giving it its own path
  would have been the bug.** The tempting shape is a second handler with its own
  size check, and a second handler is where a second set of caps quietly diverges
  from the first. It joins the text path — same cleaning, same redactor, same budget
  — and the test that proves it plants a credential inside the resource, because a
  second path would have been a second way around the seam rather than a second way
  to format a string.
- **A cap that can be defeated by a cut is a cap per CHANNEL, not per subsystem.**
  Phase 3's finding (redact before cutting) transferred intact, but phase 4 added a
  second channel and therefore a second way to get it wrong, and the ordering test
  had to grow a second half to say so. The structured cap resolves DIFFERENTLY from
  the text cap and for a reason worth writing down: an oversized structured answer
  is dropped whole rather than truncated, because truncated JSON is not smaller JSON
  — it is a different document claiming to be one, and a model would draw
  conclusions from a shape the tool never produced. **Two caps in one pipeline can
  be right for different reasons; assuming they are the same rule is how one of them
  ends up wrong.**
- **The cleaning pass earned its place on ORDER, not on tidiness.** Stripping escape
  sequences and invisible characters from a server's answer looks cosmetic, and as a
  cosmetic change it would not have been worth the lines. It is worth them because
  it runs BEFORE the redactor: every rule in `agent_core/redaction.py` matches a
  contiguous pattern, so a credential with a zero-width space in the middle matches
  nothing at all, and cleaning afterwards would have handed a model a key the
  redactor had already declined to see. The mutation is one line (clean after
  redact) and the test is one invisible character. **A hardening that only reads as
  hygiene is usually in the wrong position in the pipeline.**
- **A property test over the empty cases found a defect the eleven example tests
  missed.** "This function never returns an empty string" is one parametrized test
  over five shapes of nothing, and the whitespace-only case failed: `if text:` is
  true for two spaces, so the text was carried, the result was non-empty by the
  code's own reckoning, and the sentence explaining that the tool answered with
  nothing was skipped — a model would have read a tool that ran and said nothing.
  Fixed to `if text.strip()`. **The examples test the paths somebody thought of; a
  property tests the ones they did not, and "never silence" was the property this
  whole phase is about.**
- **A budget stops being a budget the moment there are two of anything.** Phase 3's
  8000 covered one joined string, so "the cap" and "the result's size" were the same
  number by accident. With parts and channels, a per-part cap would have left every
  document truthfully saying "8000" while a server sent ten of them. The fix is
  arithmetic, not a new constant — the structured answer settles first, the text
  takes the remainder — and `MAX_STRUCTURED_CHARS` is written as `MAX_RESULT_CHARS
  // 4` so the two cannot drift. Addison's own lines are deliberately outside the
  budget: a server must never be able to squeeze out the sentence explaining what
  it did.
- **Three phase-3 tests changed, and the ones with a note were the cheap ones.**
  Two carried "phase 4 owns this" in their own docstrings and were a pleasure to
  update; the third (the trim marker) did not, because nobody predicted the marker
  would need totals. Each was changed by making its assertion say MORE — the phase-3
  sentence is still asserted in every one — which is the only kind of edit to an
  older phase's test that should be easy to review. **Where an assertion is going to
  move, the note that says so is worth more than the assertion.**

---

## What shipped 08-07 — step 7 phase 3: a stranger's tool runs, once a person says so

[step-7-mcp-plan.md](step-7-mcp-plan.md) §4.3 owns what landed and the four
decisions taken with it: `mcp_client.call_tool`, the bounded `inputSchema`,
`McpTool.execute`, the `tool_audit` vocabulary migration, and the surface copy that
changed from "Addison can't use these" to "Addison asks you before each use."

What building it taught:

- **Phase 2's flip-point paid for itself, exactly as designed.** Turning dispatch on
  was one constant and one method body. The three things that had to be true on the
  other side of the flip — an `mcp:` id in `visible_tools(OPEN)` and never in SAFE,
  a card per invocation, a refusal at both dispatch sites — were all already
  asserted on the REGISTRATION rather than on the view, so the flip broke exactly
  the six tests whose authors had written down what phase 3 would change, and
  nothing else. **A phase gate is worth building when the next phase's diff is the
  thing you want reviewable.**
- **The cap can defeat the redactor, so the redactor goes first.** This is
  `run_command`'s 2026-07-31 finding arriving at a second source of output, and it
  arrived as a design decision rather than as a bug only because that comment
  existed to be read. Every redaction rule is anchored on a vendor prefix plus a
  minimum body, so cutting a result at 8000 characters through a credential leaves a
  head that matches nothing afterwards and travels to the provider intact. The test
  plants a key straddling the cut, and it is the only test in the tree that would
  catch the wrong order. **A finding is only reusable if it is written where the
  second occurrence will look.**
- **A row that says "granted" when nothing happened is a lie the log cannot
  correct.** `run_command`'s precedent — a command that ran and exited non-zero is
  still `granted`, because the row records the DECISION — does not transfer whole to
  a program at the far end of a network: "approved, and it never reached anything"
  is the question somebody actually asks afterwards, and no other row can answer it.
  Hence `failed`, and hence `ToolResult.audit_outcome`, which is empty for every
  native tool so no existing row changed shape.
- **The routine engine audited BEFORE it executed, and that cost it two facts.** It
  had always written its `granted` row above `tool.execute`, where the live loop
  writes it below — a divergence nobody had needed until a result started carrying
  something worth recording (what the redactor removed, and whether the call
  landed). Moved, with both failure shapes already caught above it so the row is
  written on exactly the runs it was before. **Two dispatch paths that do the same
  thing in a different order are one bug away from disagreeing about history.**
- **A tool's address is looked up when it is CALLED.** Capturing the URL at
  discovery is the obvious shape and it is wrong: between the check and the call a
  server can be removed, restored away by a snapshot, or saved again under another
  name, and a call to where it used to be is a request going to an address the
  person can no longer see. The resolver checks the NAME as well as the id, because
  the name is half of the tool id and therefore half of every grant and audit row
  keyed by it.
- **SQLite cannot ALTER a CHECK, and `CREATE TABLE IF NOT EXISTS` will not tell
  you.** The migration rebuilds the table, and its own test is that three rows of
  somebody's history survive it — `tool_audit` is excluded from snapshots and never
  pruned, so those rows are the whole record of what Addison has done. The guard
  reads the table's own DDL out of `sqlite_master`, which makes it idempotent
  without a version column to keep in step. *(It shipped as a bare rename-copy-drop,
  which survived every test and lost every row if anything interrupted it — the
  review pass above owns that finding, and it is the one to read before touching
  this method.)*
- **Two spellings of one vocabulary is a bug that only appears on upgraded
  databases.** `Store._TOOL_AUDIT_OUTCOMES` is the list, schema.sql's CHECK is the
  other copy, and a test asserts they agree — because the failure mode is a value
  that is legal on a fresh install and rejected on everybody else's, swallowed by a
  best-effort `except` on the way through.
- **A permission `detail` REPLACES the card's description** (`main._on_permission_
  request`), which is why `McpTool` declares none: the provenance sentence — this
  came from a tool server you added, Addison can't know what it will do — is the
  part of that card that may not be replaceable by a stranger's argument text.

---

## What shipped 08-07 — step 7 phase 2: Addison can see a stranger's tools, and run none of them

[step-7-mcp-plan.md](step-7-mcp-plan.md) owns the phase order and records what
landed: `agent_core/mcp_client.py` (Streamable HTTP), `agent_core/mcp_catalog.py`
(admission + the in-memory catalog), `mcp.refresh`, two new registry dimensions,
and the per-server sections on the Tools surface.

What building it taught:

- **A safety test can pass for the wrong reason, and only a mutation finds out.**
  "No MCP tool is in the SAFE view" was asserted through `visible_tools(SAFE)`, and
  it passed with `dev_only=True` deleted — because phase 2 ALSO hides these ids from
  every view via `not_callable`, and the second filter was silently carrying the
  first. Phase 3 turns `not_callable` off. The test would have gone green the whole
  way and failed open on exactly the day nobody was looking at it. It now asserts on
  the REGISTRATION (`is_dev_only`, and the dispatch refusal), which is the property
  that has to survive the flip. **Where two mechanisms produce one observable, a
  test on the observable proves neither.**
- **"Nothing is callable" had to be a mechanism, not an omission.** The tempting
  version of this phase is to register the tools and simply not write dispatch. That
  is an absence, and absences are not testable. So it is two layers with one switch:
  ids are kept out of `visible_tools(mode)` in every mode (that list is what is SENT
  to the model, so an id in it is an invitation), and both dispatch paths refuse one
  named anyway. `mcp_catalog.MCP_TOOLS_ARE_CALLABLE` is the single constant phase 3
  flips, which also means the boundary is one grep away rather than a property of
  what has not been written yet.
- **The refusal that cannot be recorded.** Every neighbouring refusal —
  `dev_only`, `forbidden`, `confined_out` — writes a `tool_audit` row, and this one
  does not. `outcome` is a CHECK-constrained vocabulary, and `CREATE TABLE IF NOT
  EXISTS` means an upgraded database keeps the old CHECK: a new value would work on
  a fresh DB and be swallowed by `_audit`'s best-effort `except` on everybody else's.
  A gate that logs on new installs and stays silent on old ones is worse than one
  that admits it logs nothing. Tracked in [KNOWN-GAPS.md](KNOWN-GAPS.md); phase 3
  owns the migration, because phase 3 is what makes the row worth having. *(Closed
  the same day: phase 3 shipped the rebuild — see the entry above.)*
- **Rolling back must never be a way to ACQUIRE a capability.** `mcp_servers` is
  snapshot-captured; the registry those tools land in is memory a restore does not
  touch. So a restore to a point before a server existed left that server's tools
  registered, owned by nothing the person could see or remove. Fixed as step (f) of
  the post-restore resync, beside `_resync_providers`, which had the identical shape
  for the identical reason. **Any subsystem that mirrors a captured table into
  memory owes `_finish_restore` a line**, and there is now a second precedent saying
  so.
- **`net_vetting` needed a verb, not a fork.** MCP is JSON-RPC over POST and that
  module was GET-only, which is exactly the moment somebody writes a second
  resolve-vet-pin loop "just for this one". It took `method`/`content` plus
  `same_origin_only`, all defaulted so the two older callers are byte-identical. The
  new parameter earned its place on its own: dropping a credential protects the
  SECRET, while refusing a cross-origin hop protects the person from being walked to
  an address they never typed — different promises, and a body-carrying caller wants
  both.
- **`unregister` had to refuse by default.** Discovery is re-runnable, so
  registrations must be replaceable — and that is the first way anything has ever
  left the registry the whole safety model is built on. An unconditional version
  would be a supported route to deleting `save_file`'s undo-enforced registration at
  runtime. Only ids registered `removable=True` are eligible; a native tool raises.
- **A status the core cannot honestly report should not exist in the core.**
  `checking` looks like it belongs beside `never`/`ok`/`failed`, but `mcp.list` and
  `mcp.refresh` answer on the same worker thread, so a list request queues behind
  the refresh and could never observe it. It is the frontend's state, set while its
  own request is out, and both protocol files say so. A source test keeps
  `STATUS_CHECKING` from being added back — machinery that defends nothing is this
  repo's own anti-pattern, and an unobservable enum value is exactly that.

## What shipped 08-06 — step 7 phase 1: MCP configuration that does nothing

[step-7-mcp-plan.md](step-7-mcp-plan.md) owns the phase order and now records what
landed: the `mcp_servers` table, `mcp.list`/`add`/`remove`, and a Developer-only
Settings section. **No client, no discovery, no registration, no dispatch** — a
saved server is inert, which is the entire point of splitting the phase off.

What building it taught:

- **The transport decision is a schema decision.** "HTTP only for v1" reads like a
  networking choice and is actually the reason nothing here can start a process:
  the row has a `url` column and no column that could hold a command, plus a
  `transport` CHECK the database enforces. Writing the decision only into prose
  would have left the next phase free to add the field; writing it into the DDL
  means widening it is a migration somebody has to justify. Both are
  mutation-proven (`test_the_database_refuses_any_transport_but_http`,
  `test_a_server_row_has_no_column_that_could_hold_a_command`), and so is the
  import graph of `rpc/mcp.py` — a module that configures a server has no business
  importing `subprocess`, the shell bridge, or the tool registry.
- **The half-mechanism you do NOT build is a decision too.** The plan's own sketch
  said "secrets to the keychain per the provider-key pattern". Phase 1 connects to
  nothing, so a token would have had no reader, no validation and no way to be
  wrong out loud — a keychain item nothing consumes is a claim, not a mechanism. So
  none was built, and the door was left where the provider-key pattern already
  points. What *did* have to ship is the half that cannot wait: the URL check, at
  the STORE boundary, because `mcp_servers` is snapshot-captured and a credential
  smuggled into an address would be copied into every later payload and sidecar in
  plain text. That check calls `rpc/providers._base_url_problem` rather than
  restating it — a second copy of a G1 rule is a second thing to keep true — with
  exactly one rule added: plain `http://` narrowed from the custom-provider case at
  large to loopback only. `net_vetting.classify_local_or_lan` was the tempting
  reuse and is wrong here: it answers True for the whole LAN because it exists to
  *disclose*, where this one *decides*.
- **"Dev-only" is not one gate, and picking which one enforces it matters.** The
  Settings section is hidden in Simple, and `mcp.add` is refused there
  independently — hiding is never enforcement. But `list` and `remove` deliberately
  answer in every profile: the rows are inert so listing grants nothing, and making
  somebody's saved configuration vanish on a profile switch is precisely the
  failure the 2026-08-06 artifact decision reversed. A tightening (removal) must
  not be trapped either.
- **The `enabled` column ships with no toggle**, on purpose and stated in the DDL:
  there is nothing to disable until phase 2 consumes a server. That is the one
  place this phase carries a field ahead of its mechanism, and it is written down
  rather than left to be discovered.

## What shipped 08-06 — a rejected key changes something, and a key is normalised where it is stored

Plan §5.2 and §5.3 (half of step 4 in
[secrets-and-keychain-plan.md](secrets-and-keychain-plan.md));
the plan owns the design and now records what shipped. §14 **decision 3 is answered:
IN** — ONE definitive auth failure marks a provider needs-attention.

What building them taught:

- **`last_check_ok` was the obvious column and was the wrong one.** The plan itself
  points at it ("written but never read"), and re-using it would have been one line.
  It answers "did the last CONNECT PING pass" — and *every* existing write of `0` to
  it is paired with `connected = 0`, so a reader cannot distinguish "never connected"
  from "connected, then revoked", which is the only state that earns the sentence. It
  also has nowhere to record that the person has been TOLD, and "told once, not once
  per turn" is half the requirement. So: a third column, `key_rejected_at`, a
  timestamp because non-NULL IS the told-once latch.
- **The dangerous interaction is one word wide.** Writing `secret_presence = 'absent'`
  on a rejection is a perfectly reasonable-sounding "the key doesn't work, so treat it
  as no key" — and it is the 2026-07-25 relay-routing bug reached by a new road,
  because `may_reach_setup_relay` fires on exactly that value. A person's next message
  would go to the external Setup Assistant relay while their key sat in the keychain.
  It has its own named test, and the mutation that writes that one word makes it red.
- **"401" is not the same fact as "auth failed", and the code did not distinguish
  them.** `ProviderAuthFailed` is raised both by the wire classifier for 401/403 AND
  locally when there is no key to send or its bytes are unusable. Marking on the
  parent would have told somebody with no key configured that their key "may have been
  revoked". A subclass — `ProviderKeyRejected`, returned by
  `exception_for_http_status` for 401/403 only — carries the narrower fact without
  moving anything: every `except ProviderAuthFailed` still catches both. Because that
  classifier is the single choke point every provider's `send` funnels through, §5.2
  needed no per-provider edit.
- **Degrading forced a documented rule to be corrected rather than worked around.**
  `providers/base.py` said auth failures never walk the chain, "the next provider gets
  the same bad key". That is true of a MISSING key and false of a REJECTED one — the
  next provider has a different key entirely — so the narrow subclass now walks (cool +
  advance, the same two lines `ProviderUnavailable` runs) and the plain parent still
  fails the turn immediately. `architecture.md` and `classes.md` were amended in the
  same commit, per docs/README rule 2.
- **"X was busy, so Addison used Y" is a lie about a revoked key**, and it would have
  been the second line the person read. The rejection note now replaces it for a
  rejected head. The test for that needed a CONTROL — an unavailable head must still
  say "was busy" — because otherwise deleting the fallback note outright passes.
- **Normalisation has to run before the unchanged-compare.** §5.3 composed with §5.4
  is not obvious: `save_would_change_nothing` compares against a STORED (already
  normalised) value, so handing it the raw paste makes `"sk-same\n"` look like a
  change, and the write that follows is the delete-then-add — the one operation in
  `keychain.rs` that can lose a key, run for a difference that does not exist. A
  source-level test pins the ORDER, and a behavioural one pins the equality.
- **A refusal that cannot be displayed is not a refusal.** Rust returns
  `Err(String)`; a Tauri command's rejection arrives at the webview as a BARE STRING,
  and the Settings row's catch is `err instanceof Error ? err.message : <generic>`.
  So the one sentence that says how to fix the paste was being replaced by "check the
  key and try again" — about a key that is not wrong. `ipc/client.ts` wraps it.
- **Two mutations SURVIVED on the first pass, and both were harness bugs rather than
  weak tests** — which is its own lesson about how easy it is to conclude the wrong
  thing from a green mutation. Dropping a redundant `AND key_rejected_at IS NOT NULL`
  from the clear query changes no behaviour (the real mutation is folding the clear
  into `upsert_provider_config`, which makes a FAILED connect clear the mark — that
  one dies). And removing a column from `_EXCLUDED_COLUMNS` without adding it to
  `_CAPTURED_TABLES` leaves it captured by neither, so the restore path is unchanged;
  the real mutation captures it, and that one dies too. **A mutation that changes no
  behaviour proves nothing about the test it was aimed at.**

Not built here, deliberately: the Settings needs-attention ROW. That is §6's
click-anchored cards, so today the mark is core-side state plus one chat-side line
and rides on no wire field.

## What shipped 08-06 — presence left the keychain, and every write heals

Steps 1 and 2 of [secrets-and-keychain-plan.md](secrets-and-keychain-plan.md), which
owns the design; this entry records what building them taught. Step 1: presence is a
column (`provider_config.secret_presence`, three-way, `present | absent | unknown`)
and no polled path asks the OS. Step 2: every credential write in `keychain.rs` is an
explicit, verified delete-then-add, foreign items self-heal after a successful read,
and an unchanged Save writes nothing.

**§14 decision 6 — the one-click experiment — is ANSWERED, and it voids a paragraph
of the plan.** Press *Always Allow* once, rebuild, relaunch: **no dialog.** Verified
this session with a genuine recompile through `npm run tauri dev`, and confirmed by
the owner. The plan's §4.2 "honest limit" said self-heal could only reset the clock to
the next rebuild on dev builds, because spike 1 (07-31) had measured an app-created
item prompting after a rebuild under the same certificate. What changed is not the
keychain, it is the signature: `sign-and-run.sh` now passes an explicit designated
requirement (`identifier "addison" and certificate leaf H"<cert>"`) instead of letting
`codesign` fall back to a per-build `cdhash`, so the rebuilt binary presents a
byte-identical requirement and the granted ACL still matches. **Self-heal is "once
ever" on dev builds too**, and spike 1's conclusion is now history. Its 29 ms
app-owned read is not history — it is the number the foreign-item threshold is
calibrated against.

What is worth carrying forward from building it:

- **`connected` could not carry presence, and the plan's §4.1 assumed it would.**
  "Did the validating request pass?" and "is a key saved?" are different facts, and a
  key that saved fine and was then REJECTED is the case that separates them. Folding
  them would have made a revoked key indistinguishable from no key — the exact
  collapse the three-way rule exists to prevent, one column over. Hence a new column.
- **The relay rule lives in ONE function, over three values, and the test asserts all
  three.** `may_reach_setup_relay` is true of ABSENT and nothing else. The plausible
  wrong version is `not present`, which is the 07-25 bug re-derived at a call site;
  it passes a two-value test and dies on the three-value one.
- **A "no row" is ABSENT, not UNKNOWN, and that needed arguing.** UNKNOWN is reserved
  for what the plan names: a read that FAILED, and a row predating the column. A
  provider Addison has never recorded a key for is a *recorded* state — it is the
  claim `provider.list` has always made by rendering it as not connected. Making it
  UNKNOWN would have meant a fresh keyless user attempting a live catalog fetch on
  every `availableRoles`, which trades an OS read for a network request.
- **The legacy implicit-connected fallback survived by being written down instead of
  re-asked.** `provider.list` used to render "a key is in the keychain with no
  connection row" as connected, computed by probing the OS. `record_secret_presence`
  persists the same answer the first time the per-turn read proves it.
- **A probe that RAISES is swallowed, so an OS-touch test must COUNT.** The first
  version of `presence_is_answered_without_touching_the_os` used a probe that raised
  `AssertionError` — and every honest presence caller wraps its probe in
  `except Exception`, because an unreadable keychain must not be a stack trace. The
  mutation that re-added the poll SURVIVED. A counter cannot be caught. This is the
  general shape: **never assert by raising through code whose job is to swallow.**
- **§5.4 cancels §4.2 unless it is qualified.** "Skip the write when the value is
  unchanged" would short-circuit the person whose item is foreign out of the very
  repair they need — pressing Save with nothing happening, which is the original
  reported symptom. The skip therefore also requires that the item is not foreign and
  that no repair has already lost the key.
- **Foreignness is detectable without a new API.** A foreign item always prompts, and
  a prompt always waits on a human — so a *successful read that waited* is a read of a
  foreign item. 400 ms, against a measured 29 ms app-owned read. One-sided: a slow but
  owned read costs one unnecessary (and verified) re-creation; a foreign read cannot
  slip under the bar without a dialog nobody saw.
- **The migration writes an item, and it must not then be healed.** The legacy
  migration spends its time on the LEGACY item's dialog while creating a fresh
  destination item under this build's identity. Elapsed time alone would have fired,
  and Addison would have run the one data-losing operation against an item it minted
  seconds earlier. Hence `OsRead.freshly_written`.
- **Delete-then-add needs a fourth read outcome.** If the re-add cannot be made to
  stick, the item is gone — and a later read finding nothing must not answer "no key
  saved", which is the cue to onboard. `KeyRead::LostInRepair` carries a plain
  sentence naming the only fix. `NothingSaved` stays a normal result; the two never
  merge.
- **What no test can reach, said out loud.** `get_provider_key`'s body needs a real OS
  keychain, so two properties are pinned by source-level backstops instead (the save
  path never calling `set_password`, the legacy delete gated on a verified copy), and
  one — caching the key *before* healing, so a torn repair cannot cost the current
  session — survived its mutation and is guarded by comment alone.

## What shipped 08-06 — dev-made artifacts are DISABLED in Simple, not hidden

Owner decision, same day. Routines and widgets created in OPEN were filtered out
of `routine.list` / `widget.list` under SAFE, so switching Developer → Simple
emptied the library and the rail: the person's own work looked deleted, and the
one row that could have said otherwise was the row being withheld. They are
listed and visibly disabled now, carrying the plain sentence dispatch already
refuses them with. [SAFETY.md](SAFETY.md) owns the rule (renamed there: *artifact
hiding* → *artifact disabling*); `CLAUDE.md`, `architecture.md`, `data-model.md`,
`flows.md` and the engineering spec were amended in the same commit.

What is worth carrying forward from building it:

- **The marker is a REASON, not a boolean.** The wire carries
  `unavailable: {reason, message}` — absent on a usable row — with `reason` an
  open slug vocabulary (`developer_abilities` today). A boolean would have needed
  a second field the first time a different cause appeared, and the parser on the
  frontend passes unknown slugs through rather than treating a cause it has not
  heard of as "fine".
- **DISPLAY ONLY, said in the code that computes it** (`rpc/constants.py`,
  `_unavailable_marker`). The refusals in `routine.run` / `widget.run` and the
  engine's per-step `dev_only` check are the enforcement and were left exactly
  where they were; **if the flag and dispatch disagree, dispatch wins**. Both
  refusals now read their sentence from the same constant the list does, so the
  surface and the refusal cannot drift into telling two stories.
- **Listing a dev row meant validating its spec against OPEN**, which is a hole
  if written carelessly. It is scoped to rows *stamped* `'open'`; a command spec
  behind a `'safe'` stamp still fails SAFE validation and stays hidden. The
  existing test for that (`..._whatever_it_claims_it_was_made_in`) is what caught
  the careless version — mutating the branch to an unconditional OPEN turns it
  red.
- **Two existing tests asserted the OLD rule** and were rewritten to the new one,
  refusal halves untouched:
  `test_dev_artifacts_hidden_in_safe_and_returned_in_open_round_trip` (now
  `..._listed_disabled_...`) and `test_custom_created_widget_hidden_and_refused_in_safe`.
  Both now assert the reason on the wire *and* that running is still refused.
- **The rail's "first thing on screen claims the source" rule grew a new way to
  break, and it is the 07-26 duplicate-source bug run backwards.** A stat widget
  made in Developer is now IN the Simple rail, drawing a reason instead of
  numbers — and it was still *claiming* its source, so the ambient connection
  block stood down for a row that shows no connections and the person's
  Ollama/Anthropic rows disappeared. Found by re-reading that rule against the
  new row type, not by a failing test; `claimsItsSource` now lets an unavailable
  widget draw without claiming. Any future row that renders something OTHER than
  its source's value has to answer the same question.
- **A parser can be dead code and nothing notices.** `normalizeRailRoutines` in
  `useWidgets.ts` was the only producer of the rail's routine marker, and every
  rail test builds its own `RailRoutine` object — so deleting the new line left
  the whole suite green. It is exported and unit-tested now; the shape of that
  gap (a normalizer whose consumers are all tested against hand-built fixtures)
  is worth looking for elsewhere.

## Measured 07-31 — two keychain spikes for the vault redesign

Nothing shipped; two claims of
[docs/secrets-and-keychain-plan.md](secrets-and-keychain-plan.md) were
**measured** before the design could rest on them (the scrutiny pass flagged
both as load-bearing and unverified). A ~60-line spike binary
(`security-framework` 3.x, modern `SecItem*` API) was signed with the
`Addison Dev` identity and driven with 4–5 s timeout guards, so a dialog
classifies as "prompted" without anyone answering it.

**Spike 1 — does creator trust survive a rebuild under a self-signed cert? NO.**
*(measured 2026-07-31 · a ~60-line spike binary signed with the self-signed
`Addison Dev` identity and NO explicit designated requirement, so `codesign` fell
back to a per-build `cdhash` — that condition is what later changed, and the
conclusion below is superseded; see "§14 decision 6" at the top of this file. The
29 ms figure survives it.)*

```
add   (build A, signed)   -> OK  25ms   added
read  (build A, signed)   -> OK  29ms   read 28 bytes        # same binary: silent
# append a no-op fn, rebuild, re-sign with the SAME "Addison Dev" identity
read  (build B, signed)   -> TIMED OUT (5s)                  # dialog appeared
```

An item created via `SecItemAdd` by one build **prompts when read by the next
build signed with the same certificate**. Without a team ID there is no stable
partition for trust to attach to, so the Chrome/VS Code zero-prompt steady
state requires an Apple-issued identity (Phase 3). The dev floor is one
explained sequence per **rebuild** (relaunches of the same binary stay silent —
consistent with the owner's original "asked only after a rebuild" report). This
**falsified** the draft's claim that app-created items give zero-dialog steady
state under the dev cert — which is exactly what the spike was for. One open
variable only the owner can measure: whether a single *Always Allow* (a
partition-list edit) is durable across rebuilds — plan §13.6.

**Spike 2 — does the data-protection keychain work under the current signing? NO, with a number.**
*(measured 2026-07-31 · the same self-signed spike binary with no provisioning
profile and no entitlements — `-34018` is a property of that signing, so this
result is void the day the app ships with provisioned entitlements, not before.)*

```
dp-add  -> ERR  11ms  Error { code: -34018, "A required entitlement isn't present." }
```

`kSecUseDataProtectionKeychain` needs provisioned entitlements; the Phase-3
deferral is now measured rather than assumed. (Also re-verified in passing:
attributes-only queries against a real item raise no dialog, and cleanup via
the `security` CLI deleted the spike item without a prompt.)

---

## What shipped 07-31 (later) — step 5.5 items 4 and 5: step 5.5 is COMPLETE

**Item 4 — output redaction + the tool-call audit trail.**

- **`agent_core/redaction.py`** — stdlib `re` only. Every rule is anchored to a
  vendor prefix or a structural marker (`sk-ant-`, `ghp_`, `AKIA`, `xox[baprs]-`,
  `AIza`, `Bearer `, PEM blocks). An unanchored "long alphanumeric" rule would eat
  git SHAs, base64 and UUIDs out of ordinary output, and **a redactor that mangles
  innocent text is one people switch off**.
- **The seam is the ORCHESTRATOR, not each provider.** There are five
  `_translate_history` functions — five places for a sixth provider to miss.
  `conversation.messages` is handed to a provider at exactly two sites, so
  redaction happens there, provider-agnostically, and a provider added later is
  covered for free.
- **Redact toward the model, never into the store** (the plan's owner decision 2,
  resolved). The wire gets a throwaway view; `conversation.messages` and the
  SQLite rows keep the real bytes, because scrubbing the person's own record would
  destroy the evidence that a leak happened. Both halves are asserted.
- **`tool_audit`** — one row per tool DECISION on every branch, at all three
  dispatch sites. Five outcomes: `granted | denied | forbidden | confined_out |
  dev_only`. It closes a real hole: `read_web_page` is LOW so it writes no
  `action_snapshots` row — **the tool most exposed to prompt injection left no
  record of which hosts it fetched** — and a refusal left none at all. EXCLUDED
  from snapshots on the `tool_grants` precedent (a restore that rewrote the record
  of what happened would be worse than no record). **This satisfies step 7's log
  dependency.**

**Two findings worth keeping:**

1. **The audit attribution was wrong by one round, and a test caught it.** The
   granted row was written *before* `execute`, naming redactions from the previous
   outbound send — but a tool's output is scrubbed on the *next* send, so that row
   described a round carrying none of this tool's output. Moved after execution and
   attributed to the result itself. The bug was invisible in the code and obvious
   in the assertion, which is the argument for asserting on values rather than on
   "did it run".
2. **The docs-drift test fired twice, correctly, on work that was otherwise
   green.** Once for the new `tool_audit` table missing from `data-model.md`'s ER
   diagram, and once because §9's restored G3 sentence had drifted more than 500
   characters from its scope marker. Both are exactly the drift those tests were
   written for, and neither would have been caught by review.

**Item 5 — design-doc §9 brought current.** Bullet 1 ("capability allow-list, not
a shell") is amended in bullet 2's own idiom — name the property, say where the
boundary moved to — rather than left standing as an aspiration. New **§9.x, "What
this does NOT defend against"**, with ten named boundaries: an attacker who can
already write to `~/.addison`, a person who approves a malicious command, prompt
injection in OPEN, exfiltration through an approved command (and why
`network-outbound` is granted anyway), the data-not-code gap, `sandbox-exec`'s
deprecation, platforms with no profile, hardlinks, multi-user machines, and the
Python-side zeroization limit. OpenClaw and Claude Code both state their
boundaries plainly; Addison's docs previously read as though the floors were
absolute.

Four mutations proven on the new guards: send-boundary redaction removed, the PEM
rule disabled, the audit's redaction naming dropped, and the view returning
originals — each kills its own test and nothing else.

---

## What shipped 07-31 — step 5.5 items 1, 2 and 3: the OPEN harness gets a floor

**G3's overclaim is closed.** `run_command` no longer executes in the Agent Core;
it crosses the ShellBridge and the shell runs it under a seatbelt profile built
from the live workspace-trust roots. The headline test is live and
mutation-proven in `shell/src-tauri/src/exec.rs`. Items 4 (redaction + audit log)
and 5 (design-doc §9) remain, and **step 7 is still downstream of item 4**.

### Items 1 + 2 — execution moved, and confined

- **`shell.runCommand`** (`protocol.py` / `protocol.ts`, hand-synced) carrying
  `{command, timeoutMs, writeRoots}` → `{stdout, stderr, exitCode, sandboxed}`.
- **`shell/src-tauri/src/exec.rs`** — the seatbelt profile, generated per call.
  Order is the security argument: `(deny default)`, broad reads, `(deny
  file-write*)`, then per-root allows, then **the data-dir denies LAST** so the
  floor beats a trusted root that contains it. The shell re-derives the data dirs
  itself; the core's `writeRoots` is an input to the boundary, never the boundary.
- **`sandboxed` is answered honestly.** On macOS a missing `sandbox-exec` REFUSES
  the command rather than running it bare; elsewhere the command runs and the tool
  prints a note above the output. A silent unsandboxed fallback would have been
  this project's own anti-pattern — a guard reporting success while doing nothing.
- **`ExecutionContext.trusted_roots` is a CALLABLE, not a list.** A list captured
  when the turn began is a trust snapshot, and the one direction it can be stale
  in is the dangerous one: a root revoked mid-turn would stay writable for the
  rest of it.

**Nine findings worth keeping:**

1. **The timeout did not exist, and a green test said it did.** `run_with_timeout`
   signalled the direct child. But `/bin/sh -c "echo x; sleep 30"` FORKS — `sleep`
   is a grandchild, so the shell died and the real work ran on, still holding the
   write end of the stdout pipe, so `drain` blocked until it finished by itself. A
   600ms budget took the full 30 seconds; `run_command`'s advertised 30s ceiling
   was unenforced for every compound command; and the shell's IPC worker was held
   for as long as the longest orphan lived. **The test passed the whole time**,
   because it asserted on the OUTPUT — and the stderr note is appended on the
   timeout path whether or not the kill landed. It was found only by noticing that
   every `cargo test` run in the session reported ~30s. Fixed with
   `process_group(0)` + `kill(-pgid)`; the test now asserts ELAPSED TIME, which is
   the property, and is the one thing an output-shaped assertion cannot see.
   Suite went from 30s to 0.6s. **If a test's subject is a deadline, assert the
   clock.**
2. **A negative sandbox test can pass because the sandbox never ran.** Mutating
   the allowlist to `/` broke the profile outright — and *both* negative tests
   went green, because a rejected profile also means the forbidden file is absent.
   The strongest possible false green, on the one boundary the step exists to
   build, and only the positive test noticed. Every negative test now writes a
   marker into a permitted path in the same command and asserts the marker landed:
   marker present + target absent means the sandbox ran and refused; marker absent
   means the test proved nothing and says so.
3. **The headline test belongs in Rust.** Python can prove the core refuses to
   *ask*; only the shell side can prove that a command which *is* approved cannot
   escape. Keeping it in Python as an `xfail` would have left the definition of
   done in the process that no longer enforces anything.
4. **No `subprocess` in the core is a property of the FILE, not of a call** — so
   it is pinned by a source test. It checks call shapes (`subprocess.run`,
   `os.system`, …) rather than the words, because the module has to be able to
   explain what was removed and why; a guard that forbids the prose is paid for by
   deleting the explanation.
5. **A test made deterministic can stop testing the wiring.** The headline
   originally read the data dir from `ADDISON_DB_PATH`, which other Rust tests
   mutate — it passed alone and failed in the full run, the worst shape of flake on
   the one test the step is judged against. The fix made `seatbelt_profile` a pure
   function of (write roots, protected dirs)… which meant every test passed the
   floor in explicitly, and **dropping `addison_data_dirs()` at the call site
   killed the floor with all six tests still green**. `the_handler_feeds_the_real_protected_dirs_into_the_profile`
   closes it by asserting on the argv the handler actually builds. Purifying a
   function for testability moves the untested part to its caller; the caller needs
   its own test the same day.
6. **Two granted capabilities that nothing needed.** The profile's non-file
   grants were written defensively — copied from the shape such profiles usually
   have — and included unfiltered `mach-lookup` and `ipc-posix-shm`. Measured
   afterwards: git, node, python, pytest and npm all work without either, and
   unfiltered `mach-lookup` is a known way OUT of a seatbelt profile (ask a system
   daemon to act on your behalf). Both removed. `sysctl-read` stays because it is
   genuinely load-bearing — without it `node` aborts in `os.GetOSInformation`
   (a `uname` call) and every `npm` invocation dies with a native stack trace.
   Under `(deny default)` each grant is a decision; the set is now pinned by
   `the_profile_grants_no_capability_beyond_the_measured_set`. **Measure, don't
   copy.**
7. **The network was denied by accident, and that was the wrong default.** No
   `(allow network*)` under `(deny default)` broke `git fetch`, `npm install`,
   `pip install` and `curl` — with a DNS error that reads as a broken machine
   rather than a policy. It also bought nothing: **the command's output already
   travels to a cloud provider**, so a profile that blocks `curl` and then hands
   the same bytes to a model over HTTPS has closed only the useful half of the
   harness. `network-outbound` is now granted deliberately, with the reasoning in
   the profile; `network-bind` is not (a model-issued command has no business
   opening a listening socket, and the 30s ceiling makes a dev server pointless).
   Exfiltration remains item 4's problem, exactly as broad reads are.
8. **A hand-synced protocol asserted on one side is asserted on neither.** Nothing
   covered the actual frame: the Rust tests called the inner functions, the Python
   tests stopped at the bridge, and `test_protocol_drift` covers the method NAME
   only. A renamed field would have passed both suites and failed the first time
   the app ran. Worse, the first version of the Rust contract test *still* missed
   it — both inbound fields are read with `unwrap_or`, so a rename silently
   becomes a default. Each field is now asserted **through its effect**: the write
   lands (so `writeRoots` arrived) and the command dies in 600ms (so `timeoutMs`
   arrived). Renaming either key on either side now fails a test.
9. **The bridge needed a third timeout budget.** A command waits on the
   *command's* deadline plus slack, not the shell's default 60s — otherwise a
   legal 45-second build is reported as a wedged shell while it keeps running with
   nobody to receive its output. `test_only_the_keychain_calls_wait_at_a_persons_pace`
   now pins three budgets instead of two.

### Item 3 — the hardline denylist

**What shipped:**

- **`policy.command_denied_path(command, data_dir)`** — the predicate. Returns
  `(token, direction)` or None, where direction is INSIDE a denylisted root or
  CONTAINS one. `denylisted_roots(data_dir)` = the existing `_protected_dirs()`
  (data dir, its snapshot sidecar, `~/.addison`) **plus** `~/.ssh`, `~/.aws`,
  `~/.gnupg`; `.env` is matched on basename, wherever it lives.
- **`tools/base.call_is_forbidden(tool, args, data_dir)`** — the generic
  dispatcher, reading a new optional `command_text(args)` on the tool.
  `run_command` is the only implementer.
- **Checked at all three dispatch sites**, above the gate: `orchestrator.py`,
  `routines/engine.py`, `rpc/widgets.py` (the Run pill), each bound to the live
  data dir by the server. A boundary one of the three does not enforce is not a
  boundary — SAFE invariant 3's reasoning applied to containment.
- **Two refusal messages, not one** — INSIDE is a dead end, CONTAINS names the
  next move ("name the folder inside it that you actually mean").

**Five findings from the build itself:**

0. **"Which data directory?" needs exactly one owner.** The mistake below was
   then made a second time, in the opposite direction: the fix left
   `command_denied_path` re-deriving the data dir from the environment, so a store
   opened anywhere but the default would have been protected in name only. Nothing
   in the tree does that today, which is precisely why it would have shipped.
   `denylisted_roots` and `call_is_forbidden` now **require** a data dir — no
   convenience default — the live server binds them via
   `WorkspaceMixin._is_forbidden_call` (the same shape as `_is_trusted_path`), and
   a source test pins the three modules allowed to derive one at all. A signature,
   not a convention, because the convention lost twice.
1. **A second copy of the floor is worse than none.** The first cut also ran
   `workspace_trust_allows` on a path-bounded tool's resolved path — a "cheap
   second layer". It re-derived the data dir instead of using the live one the
   caller holds, so under the test harness (`conftest` points `ADDISON_DB_PATH` at
   `tmp_path`) **every ordinary file in a test was judged to be inside the data
   dir**: 11 step-5 tests failed. Confinement already applies the floor at these
   same three sites, with the right data dir. The branch was deleted, not fixed.
   The failure was loud only because the step-5 suite is thorough; the same
   mistake in a less-covered predicate would have shipped.
2. **`command_text` is deliberately not `permission_detail`.** The detail is
   capped at `MAX_PERMISSION_DETAIL_CHARS` (120) for the card and the Activity
   Panel. A denylist reading the capped string stops seeing the dangerous path of
   any command long enough to push it past the cap — a hole you get for free by
   reusing the obvious accessor. Pinned by
   `test_a_long_command_is_scanned_in_full`.
3. **`{` and `}` must not be token separators.** Splitting on them tears
   `${HOME}/.addison` into three pieces, none of which resolves to the data dir.
   Caught by the `${HOME}` case in the forbidden list, which was written before
   the code.

**The known cost, and it is deliberate:** the ancestor direction means `ls ~`,
`ls .` and `ls /` are refused outright, not carded. Read and write are not
distinguishable in a `shell=True` string — that is #48's lesson, three times over
— and `rm -rf ~` takes the recovery floor with it. Naming a subfolder works, and
the **two refusal messages** exist for this: a CONTAINS refusal says which move to
make next, so the model corrects in one turn instead of reporting the task as
blocked. One shared message made every `ls ~` read as a dead end.

**Now that item 2 has landed, this direction is scaffolding.** The kernel refuses
`rm -rf ~` while allowing `ls ~`, which is the distinction the string could never
make. `command_denied_path` says so in its own docstring: delete the CONTAINS
direction and `_names_a_directory` with it, rather than tuning either. Left in for
now only because nothing measures how often it fires — that is item 4's audit log.

**One test was passing for the wrong reason** and is worth naming: the
attached-short-flag vector `grep -f/Users/x/.ssh/id_rsa .` matched on its trailing
`.` (a CONTAINS hit against home), so the flag path it was written to cover went
entirely untested. It is now spelled against the real home with no trailing token.
A vector that passes for the wrong reason is worse than no vector — it occupies
the slot where the real check should be.

Every guard line was mutation-proven in a scratch copy outside the repo (seven
Python mutations: each of the three sites, both directions of the predicate, the
`.env` rule, and `command_text` reading the truncated detail; plus three Rust
mutations against `exec.rs`).

---

## What shipped 07-20 — Phase-2 step 1, the G3 rollback floor

The floor everything else leans on. The motivating story is worth re-reading in
amendment §1 before touching any of it: a non-technical user asked his AI tool to
"make the models run as cheaply as possible", it broke his setup permanently, and
the built-in rewind did not fire. **The one requirement that outranks every other
line in that subsystem is "restore always works, even from a broken config."** The
test of that name is in `tests/test_snapshots.py` and heads the file on purpose.

**Storage.** `config_snapshots` in `agent_core/memory/schema.sql` — 12 columns,
two indexes, and **two `RAISE(ABORT)` triggers** that refuse to delete an
`undeletable = 1` row and refuse to clear the flag. Permanence lives in the
database, not in a `WHERE` clause a future query can forget. `ConfigSnapshot` /
`RestoreResult` in `agent_core/snapshots/model.py` mirror it 1:1.

**Capture scope.** `agent_core/snapshots/scope.py` — a declared table set *and* a
declared column set, both with completeness tests. Adding a Phase-2 table or a
column to a captured table turns the build red until you decide, in code, whether
it is captured or excluded. That is not pedantry: restore is replace-all with an
explicit column list, so an uncaptured new column would be silently reset to its
default **by the recovery path** — a restore would wipe the routing strategy or
the Custom guard toggles you are about to add.

**The manager.** `agent_core/snapshots/snapshot_manager.py` (large, and roughly
half of it comment and docstring — that ratio is intentional here) —
`capture` / `mark_verified_working` / `restore` / `restore_last_working` /
`last_working_target` / `list` / `delete` / `mint_anchor` / `prune`, plus two
store-free module functions for disk recovery. It imports stdlib plus the two
schema-mirroring leaves and **nothing else** — no provider, router, profile,
policy mode, registry, or gate. Retention (50 rows / 30 days) and the payload
version are **module constants, not settings**, so nothing the model can write
shrinks the rollback window.

**Two writes, always.** Every payload goes into the row *and* into a `0600` JSON
sidecar at `<db_dir>/snapshots/<id>.json` (dir `0700`). That is the answer to "the
database itself is the broken thing": `snapshot.list` and
`snapshot.restoreLastWorking` are the only two RPC methods **exempt** from the
server's build-failure short-circuit, and with no usable Store they are served
from the sidecars. A restore on that path renames the damaged file **aside**
(`<db>.damaged-<epoch>` — never deletes it) and rebuilds, in the **same session**,
with no restart. Three tests cover it, including the byte content of the renamed
file. `snapshot.list` deliberately does *not* rename anything — a look must not
cost you your database.

**RPC + wiring.** Five `snapshot.*` methods, the `shell.appBuildRef` Core→Shell
call (Rust: `shell/src-tauri/src/app_build.rs`), `agent_core/rpc/snapshots.py`
(the sole snake→camel mapper), and six touch points in `main.py` — including
moving `_ensure_built()` inside the worker's error handling, which was a real hole:
a broken store used to hang the process forever.

**Hooks.** Seven auto-capture sites + one verified-working site, one line each,
with a deliberate **capture-failure policy split**: the four hooks whose old
content exists nowhere else (delete a routine / widget / note, change a note)
**refuse the change** if the snapshot cannot be taken; the three recoverable ones
proceed and raise a **sticky warning** that only a successful manual save clears.

**Frontend.** The Settings **"Restore points"** section (never called
"Snapshots" in any user-facing string), placed directly under Profile, plus the
Restore-points modal and the Snapshots surface that share its rows by value. The
restore action is **accent, never the rose `danger` token** — a recovery is not a
destruction — with a two-step inline confirm carrying the consequence copy plus a
profile-change sentence and a genesis sentence when they apply, the target always
named with its timestamp and **kept on screen through the confirm**, and the
blocky **Permanent** tag (small caps on a 2px accent rule) with no Remove control
on anchors. QA steps: **TESTING-CHECKLIST §13a**.

**Two decisions worth internalising before you extend it:**

- **Restore is an RPC path, never a registry tool, and never passes the permission
  gate.** A gate that could deny a restore would make "the restore path is itself
  unbreakable" false. The only model-facing snapshot surface that will ever exist
  is the **LOW, capture-only** `snapshot_now` tool — add a row, nothing else.
  (Shipped 2026-07-24; an AST source test holds it to `capture` alone.)
- **`created_in_mode` never filters a snapshot query, in any mode.** The
  engineering spec's DDL comment said the column "mirrors existing artifact
  hiding"; that was **overridden, not followed**, and both the spec and
  `data-model.md` now say so. Following it would hide the way back from exactly
  the user who most needs it: weakened a guard in Custom → broke something →
  switched to Simple → opens Restore points to an empty list. Two tests hold the
  line, one behavioural and one **source-level**
  (`test_no_snapshot_query_filters_on_created_in_mode`) that reads the SQL and
  fails on a filter position — because a behavioural test only proves today's
  behaviour, and would not stop someone adding `AND created_in_mode = ?` next
  quarter.

## What shipped 07-24: step 2 — Custom profile + guard model + the G4 anchor caller

Built from a frozen contract that was **adversarially reviewed before any code**
(verdict: amend-then-build; six MUST-FIX findings integrated — the review caught
two real safety holes the draft missed: a targeted restore silently re-weakening
guards, and a session destructive grant surviving a switch back to Simple).
Coordinator personally reproduced four mutation kills (CUSTOM→SAFE derivation,
session-grant leak into `_grants`, guards.set ignoring a mint failure, dedupe
removal); every regression test in the wave is mutation-proven.

- **`ProfileId.CUSTOM`** (profiles.py) — Developer's surface, `advanced: true` on
  the wire; the frontend hides it behind an "Advanced…" disclosure + two-step
  confirm. `mode_for_profile`: DEVELOPER **or CUSTOM** → OPEN (policy.py; a
  SAFE-derived Custom would have nothing to tune). `profile.get`'s `mode` stays
  `'safe'|'open'` — never `'custom'`; the guard panel keys off the profile.
- **`GuardConfig`** (policy.py) — two closed vocabularies with total strictness
  orders: `guard_destructive_card` `per_invocation` > `session`;
  `guard_auto_grant_scope` `none` > `non_destructive` > `everything`. Defaults ≡
  today's OPEN, and `authorize(guards=None)` ≡ defaults — that equivalence is the
  Simple/Developer freeze, proven by the whole pre-existing suite passing
  untouched. Guards are EFFECTIVE only under Custom (`_effective_guards`, the ONE
  resolution function all three authorize call sites read — orchestrator, routine
  engine, widget Run pill).
- **"Ask once" lives in a dedicated set.** A `session` destructive approval is
  remembered in `_destructive_session_grants`, NEVER `_grants` — the SAFE
  `check()` path reads only `_grants`, so the grant is structurally invisible to
  Simple. Belt: `profile.set` now calls `revoke_all()` + `clear_denials()` on
  every switch (the revoke_all docstring's own posture principle).
- **`guards.set` is the G4 anchor caller** (rpc/guards.py): validate → compute
  weakenings → **mint the anchor FIRST** (refuse the whole set, nothing persists,
  if the anchor cannot mint) → persist. `mint_anchor` gained fingerprint
  **dedupe**: one anchor per distinct weakening save; weaken→tighten→weaken
  churn cannot grow an unbounded permanent list, and a crash between mint and
  persist re-mints nothing on retry.
- **Restore re-weaken disclosure** (rpc/snapshots.py): when a restore lands on a
  weaker guard posture under Custom, the result's `detail` says so in plain words.
  No new anchor — the original weakening's anchor is undeletable and still there.
- **`created_in_mode`:** artifacts stamp `'open'` under Custom (Custom IS
  OPEN-derived; the three hard-coded `== 'open'` hiding/refusal filters keep
  working, so Custom-built widgets/routines hide in Simple). ONLY
  `config_snapshots` records `'custom'` (main.py `mode_ref`) — display-only, C6
  never filters.
- **Frontend:** Advanced disclosure + two-step confirm on the profile card; the
  Custom guard panel (two guards only — the floors are structurally absent from
  the panel), frozen plain-language copy including the honest cost of "Never ask";
  weakening saves get the permanent-anchor confirm, tightening saves go straight
  through; `ipc.restoreSnapshot` finally has its caller — per-row "Restore this
  one" on PERMANENT rows only (owner decision 2026-07-24).
- Also: dropped the never-written `RestoreResult.providers_needing_a_key`
  (loose end resolved: the keychain probe computes names itself); amendment §13
  **Q3 closed** as the lean (reachable from any profile, deep + questioned).

**The post-build rigor pass (same day) — read this before trusting the wave
above.** A second adversarial reviewer attacked the finished code with
reproduce-don't-read rules and found **one real bug the first review, both build
agents, and 25 green tests all missed**: `auto_grant_scope='none'` — the
STRICTEST-labelled option — routed destructive calls into the coarse SAFE flow,
so one approved `ls` silently covered every later `rm -rf` with no card and no
command text, under copy promising "asks before every kind of action", and
counted as a *tightening* so no anchor was minted. Fixed: destructive never
enters the coarse flow under any scope; the scope knob governs everyday actions,
the card knob alone governs destructive ones ('everything' stays the one explicit
override). Two regression tests pin it, both proven red against the reverted fix.
The lesson is the standing one: the test gap was structural — the only
scope-'none' test used a non-destructive tool, and its one-arg stub would have
TypeError'd on the destructive path, so the wrong assumption protected itself.
Also from the pass: `guards.set` now persists its two keys in ONE commit
(`Store.set_settings`; half-a-pair after "nothing was changed" was a lie
waiting); anchor dedupe refuses to confirm an anchor whose payload no longer
loads (row rotted + sidecar gone → fresh mint); the D7 docstring now names the
one path that legitimately skips the notice (sidecar cold-start — the
pre-restore posture is unknowable there); the "Ask once" copy now states its
real breadth ("anything else it does goes ahead without asking"); TESTING-
CHECKLIST **§13b** is the manual QA script for all of this; and a **live
end-to-end driver run** (the HANDOFF pattern, 17 checks, including one real
haiku turn) verified the whole Custom flow over real JSON-RPC — dispatch,
anchor, dedupe, D7 notice, C6 under SAFE, and `snapshot_now` writing through
`main()`'s late-bound holder.

## What shipped 07-24: step 3 — routing strategies

Built from a contract that took TWO adversarial review rounds (round 1:
REDESIGN — the drafted quality-first silently overrode the user's standing
default model, the fallback trigger assumed an error classification the
providers don't have, and per-turn vs per-send was undefined; round 2 on the
redraft: AMEND-THEN-BUILD with five sharper fixes). **Owner decision: Balanced
is CUT from v1** — the drafted version was provably identical to cost-first at
two-model pools; amendment §10.1 carries the note.

- **Stage 0, load-bearing:** `ProviderUnavailable` / `ProviderRequestRejected`
  / `ProviderAuthFailed` in `providers/base.py`, raised by every provider from
  the existing collapse points with byte-identical messages. Fallback advances
  ONLY on Unavailable — Rejected/Auth fail the turn at once (the next provider
  would get the same bad request / same missing key). Providers also accept a
  per-call timeout override — the budget's teeth.
- **Chains** (`resolve_chain`, pure, store-free): the HEAD of every
  cloud-containing chain is the user's standing default (`selected_primary`) —
  strategy orders only the tail, so the freeze is structural and rank can
  never override a deliberate weaker-model choice. One resolution path:
  absent key ≡ quality_first (a dual path made the Simple toggle's round-trip
  observable). Unknown-rank models sort behind the head, never demoted. All
  Ollama candidates share `provider_id="ollama"`.
- **The attempt loop** (orchestrator): per-send continuation, never restart;
  cross-provider mid-turn advance FORBIDDEN in v1 (foreign tool_use history
  into another vendor's translator is unverified — the same-provider case, two
  Ollama models, is allowed); `_COOLDOWN_SECONDS=60` and
  `_FALLBACK_BUDGET_SECONDS=120` as module constants, the budget enforced as a
  REAL per-attempt deadline (`timeout=min(default, remaining)`) so a single
  hanging candidate cannot blow it — the test uses a genuinely BLOCKING mock,
  because an instant-fail mock cannot see this gap.
- **`local_only` outranks everything**: resolved BEFORE the Setup-Assistant
  relay branch (the relay is a cloud call), and an explicit per-message cloud
  pick under local_only is refused in plain words — the privacy invariant has
  no per-message bypass.
- **`answeredWith` + the chip**: `on_answered` carries (model, label, free,
  routed) with `routed ≡ answering ≠ explicit pick` — an explicit pick that
  fell forward to a free model DOES chip. `on_usage` now carries the RESOLVED
  per-attempt identity, **fixing a pre-existing bug**: `_usage_identity`
  attributed every routed turn to the catalog default.
- **`routing.*` RPC**: closed vocab, custom chain validated at set (unknown
  ids refused), hook split — a strategy change snapshots-and-proceeds
  (`routing_change`), a custom-chain OVERWRITE refuses if the snapshot fails
  (user-authored content, the note-overwrite policy). Simple sees the one
  toggle; Developer/Custom the full picker + chain builder.
- Verified: mutation-proven throughout (coordinator personally killed the
  freeze head, the budget threading, and the local_only interlock); an
  11-check live driver over real JSON-RPC including a real model turn carrying
  `answeredWith`.

**The step-3 post-build rigor pass (same day).** The adversarial hunt confirmed
the load-bearing invariants non-vacuous and found one real (low-severity) bug
plus three smaller items, all fixed and kill-verified:
- **The internal connect-retry could double the fallback budget**: a
  ConnectTimeout inside `request_with_retry` retried once BEFORE the
  orchestrator regained control, so one candidate could run ~2× its deadline.
  Now a caller-supplied deadline disables the internal retry — the chain IS the
  retry (`allow_retry=timeout is None`, all five providers); standalone calls
  keep today's robustness (both pinned).
- **The vanished-custom-chain-id note existed in the contract but not the
  code** — the skip shipped silently. Now one plain Activity note names the
  skipped models, tested over the wire.
- **A head cooled by a previous turn suppressed the fallback note** —
  `preferred` is now the pre-cooldown chain head, so quiet substitution is
  impossible.
- The contract-NAMED `test_local_only_never_reaches_the_relay` now exists
  (keyless Simple + relay redirect armed + interlock forces LOCAL; kill-verified
  loud).
- **Known gap, deliberate:** a Simple user whose stored strategy is
  `local_only`/`custom` (set in a Developer session) sees the two-option toggle
  with neither option active while the real strategy still governs — a
  migration/UX edge needing design, not a silent bug; the strategy is honest in
  `routing.get`. Decide the toggle's third state when step 4 touches this
  surface.

## What shipped 07-24: steps 4 + 5 — free-model endpoints & the coding harness

Built by four agents in isolated worktrees from two frozen contracts, each
adversarially reviewed twice before a line was written, then merged four ways by
hand. **Read the post-build pass below before trusting any of it** — it is where
the real defects were.

**Step 4 — free-model endpoints, add-by-prompt, "make it cheaper".**
- **`agent_core/net_vetting.py` is new and is the load-bearing piece.** The WHOLE
  pinned-request execution moved out of `read_web_page` — not just the URL
  rewrite. Reusing only `pinned_url` would make httpx verify the certificate
  against the IP literal and refuse every legitimate HTTPS server, or tempt
  someone to weaken verification, which is a worse hole. The vetting DECISION is a
  parameter (`allow_private` / `require_default_port`), so the public-web policy
  and the user's-own-LAN-host policy share one mechanism; the plain sentences are
  a parameter too, because the two callers speak to different audiences.
- Both flows are **propose/confirm RPCs whose fields are core-derived or canned** —
  the turn reply never carries a model-authored actionable payload. `endpoint.propose`
  reads the CURRENT turn's `role=="user"` messages only (a model that echoes
  `https://evil` into its answer must not become the extraction source), and only
  a short add-endpoint-shaped utterance arms a card. `costPlan.propose` is entirely
  constants.
- `costPlan.apply`: validate → skip if already in effect → **snapshot, REFUSING
  the whole apply if it cannot mint** (a deliberate new hook class — a compound,
  conversationally-initiated degradation for the at-risk persona whose only
  recovery is the restore point; `routing.set` still proceeds-with-warning, and
  the asymmetry is noted in both places) → **one atomic `Store` commit** so a
  half-applied plan is impossible.
- The free chip stays **Ollama-only**: no cloud `CloudModel.free` is True in v1, so
  the chip asserts a cost fact Addison can actually establish. Google's free tier
  is *information* under the provider row, not a routing flag.

**Step 5 — the coding harness + workspace-trust.** Two typed, OPEN-only,
path-bounded file tools, a `workspace_trust` table, a `workspace.*` RPC, and three
new Rust bridge methods. Four things are worth internalising before extending it:

- **Confinement is a DIFFERENT PREDICATE from prompting, and that was the central
  gap in the first draft.** "Is this path inside a trusted root" (permission to
  TOUCH) is not "may the card be skipped" — and a LOW read never cards in OPEN
  anyway, so the gate's `trusted` bool alone confines nothing. The CALLER
  (orchestrator / routine engine) resolves `affected_path`, checks it, and
  **hard-refuses before `execute`** for LOW and MEDIUM alike.
- **Resolve ONCE.** `affected_path` realpaths exactly once; the resolved value is
  what the caller checks AND what `execute` acts on, handed over via
  `ExecutionContext.resolved_path`. Re-reading `args["path"]` inside `execute`
  reopens a TOCTOU gap: confinement approves one path, the write lands on another.
- **`dev_only` split into two dimensions** (`open_only` = visibility,
  `allow_missing_undo` = the exemption from the undo-at-registration check),
  because `write_project_file` must be BOTH hidden from SAFE AND undo-enforced,
  and the old single flag could not say that.
- **Owner decision 2026-07-24: trust suppresses cards ONLY for the typed,
  path-bounded, undoable file tools. `run_command` ALWAYS cards.** Its
  `affected_path` is None, so confinement never governs it and it can never be
  trust-suppressed. That is what makes amendment §8.2's two bullets simultaneously
  deliverable. **§8.2 and design-doc §9 are both annotated as superseded** — trust
  is NOT snapshotted (see below), and the OPEN tools scope by trusted root rather
  than by file picker.
- **Trust is EXCLUDED from snapshots**, on the `tool_grants` precedent: standing
  consent that suppresses cards is a grant in all but name, and restoring one the
  user had revoked would be privilege escalation delivered by the deliberately
  ungated one-action restore button.

### The step-4/5 post-build rigor pass — read this before trusting the above

Three adversarial reviewers attacked the finished tree with reproduce-don't-read
rules. They found **seven real bugs that two rounds of contract review, four build
agents and 847 green tests all missed**, plus a cluster of tests that proved
nothing. Every fix below is mutation-proven: **27 Python mutations applied, 27
killed**, plus 2 Rust mutations killed, each reverting one fixed line in a scratch
copy outside the repo. The coordinator reproduced the two worst personally, from
scratch, before touching anything.

**The two that would have shipped a broken feature and a leaked key:**

1. **The API key was forwarded to whatever host a redirect named.** `open_vetted`
   built the header dict once and threaded it through every hand-followed hop, so
   a custom server — or anything able to answer 302 for it — harvested the user's
   key verbatim. The aggravating detail: **httpx's own follower strips
   `Authorization` cross-origin** (`Client._redirect_headers`), so the hand-rolled
   loop that replaced it was strictly weaker than the library it displaced, on the
   one axis that carries a secret. Fixed with a SEPARATE `credential_headers`
   parameter rather than a "strip anything called authorization" rule, so the next
   caller to put a secret in a header inherits the protection by construction
   instead of by naming their header correctly.
2. **`pinned_url` dropped the PORT**, so `http://localhost:11434/v1` connected to
   `127.0.0.1:80` — a different service on the same machine, carrying the Bearer
   key to it. Harmless while the only caller required the default port; live the
   moment step 4 allowed any port, which means **the entire feature — Ollama
   :11434, LM Studio :1234, llama.cpp :8080 — could not work at all.** The default
   port is still omitted, exactly as a browser omits it, so `read_web_page`'s
   requests are byte-identical to before.

**The rest, each reproduced before it was believed:**

3. **A NUL byte in a `path` argument crashed the whole turn** — `Path(raw).resolve()`
   raises, and both confinement call sites sit OUTSIDE the handling that exists so
   "a tool failure is a failed STEP, never a crashed turn". On the routine path it
   left the run recorded as `running` forever. Now an unresolvable path returns a
   **sentinel, never `None`** — because `None` means "not a path tool" and skips
   confinement entirely, which would have let a malformed argument walk past the
   boundary into the gate.
4. **`workspace.list` was read as `{roots}` while the core sends `{folders}`**, so
   the trusted-folder list rendered permanently empty in the shipped app: no "Stop
   trusting" button, standing consent unrevocable from the UI. Both suites were
   green — Python asserted `folders`, vitest parsed a hand-built `{roots: […]}`
   literal, and **neither could see the other**. Fixed, and closed structurally: the
   generated payload fixtures now cover `workspace.list`, `costPlan.propose` and
   `endpoint.proposeFromConversation`, so a new payload a parser consumes gets an
   artifact both sides share. *Add a fixture for every new payload.*
5. **A turn-scoped "Not now" was ignored inside a trusted folder** (`_auto_grant`
   never consulted `_denied`). Nothing escalated — the call was card-free anyway —
   but a person was shown a card, said no, and watched Addison edit a file in the
   same turn. Consent honesty, not privilege.
6. **Workspace trust silently overrode Custom's strictest guard.**
   `auto_grant_scope='none'` is the maximum-asking option and its copy says Addison
   asks about everything; trust made destructive writes card-free under it, and a
   tightening mints no anchor, so nothing marked the moment. **Exactly the defect
   shape the step-2 rigor pass found** — the strictest-LABELLED option carrying the
   quiet hole. Simple/Developer are byte-for-byte unchanged (their guards are the
   defaults).
7. **`trust_env=False` was applied to the stock OpenAI connect**, which is a module
   constant, not user input — so connecting an OpenAI key behind a corporate proxy
   would fail while chat kept working. A freeze break dressed as hardening.
8. **The connect card's worst case went from ~20s to ~120–240s**: the walk tries up
   to `MAX_ADDRESS_ATTEMPTS` addresses per hop across every redirect, and the
   idempotent retry then re-ran the whole thing. A per-socket timeout is not a
   budget — same lesson as the step-3 fallback budget — so `total_timeout` now
   bounds the whole walk, **including the address loop inside each hop** (bounding
   hops alone left most of the wait unbounded; the first version of this fix did
   exactly that and its own mutation test caught it).
9. **The Rust shell's data-dir floor was defeatable by a DANGLING symlink** — the
   target does not exist, so canonicalization stopped at the link's own harmless
   location while `fs::write` followed it and planted a file in the G3 sidecar
   directory. The Python floor caught it, so this was never a live breach, but the
   comment claiming defence-in-depth was false. `canonical_lossy` also only checked
   the IMMEDIATE parent, so any missing intermediate component left the candidate
   un-canonicalized while the protected dir was canonicalized — on macOS, where
   `/tmp` and `/var` are themselves symlinks, that is the ordinary case.
10. **Smaller, all real:** the `_ADD_ENDPOINT_HINTS` gate matched **substrings**, so
    "add" matched **Addison** — the app's own name — and "api" matched "therapist";
    `"Addison, what is <url>?"` armed a connect card, and deleting the entire gate
    left the suite green. Now word boundaries, with two hints dropped for carrying
    no signal. The case-insensitive URL regex fed a case-SENSITIVE scheme check, so
    a phone's `Http://…` was refused with "Enter a web address that starts with
    http://" — false about the address just typed. The protocol drift test's
    `[a-z]+\.` namespace pattern matched **neither** `costPlan.*` constant, so the
    one guard standing in for codegen ignored the two newest methods.
11. **Tests that proved nothing.** Five mutations survived the *entire* 847-test
    suite, and the reason was structural: **pytest's `tmp_path` is already fully
    realpath'd**, so in every step-5 test the raw argument and the resolved path
    were byte-identical and the whole resolve-once mechanism could be deleted
    unnoticed. The fix is a symlinked alias in the fixture, so the tool is handed a
    path it must normalise. Also unwatched: `apply_cost_plan`'s atomicity (the one
    property its dedicated `Store` method exists for), R7's "both halves must
    hold", and the routine engine's confinement, which had only a positive test.

**Frontend integration completed in the same pass:** the step-4 cards were built
and tested but rendered nowhere — now wired through a `useOffers` hook mirroring
the widget propose→card→confirm flow, triggered off the USER's text only (the
model's reply must never arm a card; the core enforces the same rule). The Google
free-tier line was an `<a href target="_blank">` **that could not open anything** —
the Rust shell registers exactly four commands for the webview and none is
`openExternal`, and `Markdown.tsx` states the standing rule that the webview never
opens URLs itself. Its test asserted the `href` and passed while the control was
dead. Now selectable mono text the person can copy, with a test pinning the absence
of an anchor. A throwing post-turn drafter also used to stamp a **successful** turn
`failed: true`; isolated now.

## What shipped 07-24 — the security + test-hardening wave (#48, #49)

After step 1 merged (#47), a test-quality measurement turned up a **live security
bug**, which is the reason this wave exists.

**#48 — `run_command` auto-granted destructive commands (LIVE BUG, fixed).** In
OPEN mode the gate auto-granted anything the tool's classifier called read-only,
and that classifier was defeatable three ways, each a character or flag its
blocklist did not anticipate:

```
ls\nrm -rf ~/x      shlex treats \n as whitespace, so it read as a lone `ls`
ls & rm -rf ~/x     the metachar list had && but not bare &
find . -delete      an allowlisted reader with a destructive primary
grep -rf /etc/x .   a short flag defeated by bundling
file -Cm /tmp/x     an allowlisted reader that WRITES a compiled magic file
```

The blast radius is the filesystem, which is **outside G3** — an `rm -rf` is not
undoable. **Owner decision: statically deciding whether an arbitrary shell command
is read-only is a losing game, so the auto-allow was removed rather than patched.**
`is_destructive` now returns `True` unconditionally; every command raises the
per-invocation card showing its exact text. The classifier, the read-only
allowlist and the metacharacter list are **deleted** — dead once nothing
auto-grants, and their absence removes the false confidence that any of it was
trusted. Cost is a card on every command including `ls`; that is the intended
trade. An argument-allowlist was drafted and rejected — a hardening round showed
even that was defeatable, which is what drove the decision.

**#49 — the test gaps that would let a floor breach ship green.** A triage pass
reproduced every candidate against the real code first and found **no further live
bugs**; everything below was correct code with nothing watching it.

- **`keychain.rs` / G1 — the headline.** Two tests built a `json!` literal in the
  test body and asserted on it, never calling the real `handle()`. So adding the
  ed25519 **private seed** to the real `getDeviceKey` response **passed all 31
  Rust tests** — on the most sensitive value in the system, in the highest-trust
  process. Response builders are now extracted (behaviour identical) and the tests
  assert over them, plus a sweep serialising every keychain response and asserting
  the seed appears in none. Verified failing with the seed added.
- **dev-only ⟹ OPEN-only, enforced at DISPATCH.** `visible_tools(SAFE)` hides
  dev-only tools from the *model*, but hiding is not enforcing: a `tool_use`
  naming a hidden id still reached `registry.get()`, and the gate does not check
  dev-ness. A dev-only tool **with no self-check executed under SAFE** through both
  dispatch paths. The boundary held only because `run_command` refuses inside its
  own `execute` — a convention tool #2 would not inherit, with steps 5, 7 and 8 all
  adding dev-only surface. Both dispatch sites now consult
  `registry.refuse_if_dev_only_outside_open()` **before the gate**, so nobody is
  asked to approve something that was never going to run.
  `tests/test_dev_only_boundary.py` drives a rogue HIGH dev-only tool through both
  paths in both modes — and asserts it **still runs in OPEN**, because breaking the
  harness would be worse than the hole.
- **undo substance.** `undo = "a string"` registered at HIGH straight into the SAFE
  view, where it would fail at the moment somebody needed to reverse something. Now
  refused. A *callable* no-op cannot be caught statically — the comment says so
  rather than implying otherwise, and a round-trip test is the honest answer.
- **repo-wide G2** (`tests/test_g2_no_self_trigger.py`): the only test pinning
  "Addison never triggers itself" AST-scoped `snapshot_manager.py` alone. Now every
  core module, with the rule stated as *"nothing that fires work on a SCHEDULE or
  after a DELAY"* rather than a ban on concurrency — so the legitimate worker
  thread, `Event` waits and blocking `queue.get()` stay green and the test does not
  get deleted by the next person it annoys. Its anti-vacuity check pins the
  **subpackages** covered, not a module count: a count lets you drop `providers/`
  entirely and stay above the floor.
- **`shell_bridge`** (killed 0 of 3): error frames, timeouts, and
  `get_provider_key` now covered. Its G1 retention test checks the instance, the
  **class**, and the **module** namespace — the plausible mistake being to port the
  Rust shell's sanctioned session cache into the core as `type(self)._cache`.
- **`rpc/widgets`** (killed 0 of 2): both SAFE-enforcement call sites, asserted
  against the widgets **table** rather than `widget.list` — the render filter hides
  the mutation's row, so the list looks identical either way.

**Also #49: `ruff` is pinned to `>=0.15,<0.16`.** CI failed with 183 lint errors
while the same tree was clean locally. Not the code: `pyproject.toml` asked for
`ruff>=0.6`, CI installs from it, and **ruff 0.16.0 shipped with more rules on by
default** — verified at **182 errors against an unmodified `master`**. The first PR
opened after the release inherited a failure it did not cause. Raising the bound is
now deliberate: bump it, run ruff, and adopt or configure the new rules in the same
change. The 182 are a separate decision (see Known gaps) and **must not be
bulk-fixed** — many `BLE001` hits are the deliberate broad `except` in the recovery
paths, where swallowing is the point.

## What shipped 07-25 → 07-26 — the dark v4 redesign wave (PR #58)

**Merged to `master`** as `a22badd` on 2026-07-26. The commit-by-commit account
that used to live here is in git; what survives below is only what a later
session still has to honour.

**Design authority: `docs/design-brief-dark/`.** `README.md` + `prototype.html`
are the designer's reference and **`IMPLEMENTATION.md` records the binding
prototype→app mapping**. Read that file before restyling anything — it is where
"demo content is never shipped, real features are restyled and never de-wired" is
written down.

**Rules the rigor pass (`1241026`) established — these are safety, not polish:**

- **A pending consent card is hoisted above any modal/drawer scrim, and the
  modal's focus trap deliberately includes it.** A consent surface stranded
  behind a scrim is unanswerable, which makes it a safety failure, not a cosmetic
  one.
- **The restore footer's undo promise is mode-scoped** (`footerNote` in
  `RestorePointsModal.tsx`). `run_command` is SAFE-2's one explicit exemption
  under OPEN, so a blanket "everything can be undone" contradicted the profile
  card two sections away.
- **The one-action restore names its target and timestamp through the confirm.**
  Losing that was a *regression against `master`*, on the G3 surface itself.
- Light-mode text meets AA; a reveal never scroll-jails a reader who has scrolled
  up; the favicon is a raster rendered from a checked-in master so the mark does
  not depend on the rasterising platform's fonts.

**What the tests were doing — the standing failure mode, found again.** The
frontend suite went 238 → 302 because `Composer` **had no test file at all and
all 11 mutations against it survived**, the reduced-motion guard could be deleted
with the suite still green, and **two G3 tests passed while restoring the wrong
snapshot.** Same shape as the step-1 finding. Assume it is still true somewhere.

**`ee38dbe` also redefined Phase 3.** `docs/phase-3-review-surface-plan.md` (a
Developer/OPEN review surface, approved 2026-07-25, blocked at the time on steps
6–8 — 6 has since landed on 2026-08-06 and 7 on 2026-08-07) is now
part of that phase alongside packaging, signing, notarisation, the updater,
binary restore and Secure-Enclave identity. The redefinition was written back
into the four documents that had scoped Phase 3 the old way.

### Shipped: thread windowing (`839bcff`)

**This section previously said the opposite.** Thread windowing came in with #58
and is on `master`; the `windowed-thread` branch and its `8503b18` no longer
exist. If a doc or comment implies the thread renders the whole conversation, it
is wrong.

Opening a conversation used to render react-markdown (and mermaid) for every
settled message in one commit. The thread now renders a trailing window of 30
rows and extends upward by 30 when the reader reaches the top, holding scroll
position across the prepend — 272 DOM nodes instead of 3602 on a 400-message
conversation, and 30 markdown parses instead of 400. Nothing below 30 messages
changes. The window is a count of rows hidden at the **top**, never a trailing
slice: a message arriving mid-stream must lengthen the window at the bottom
rather than drop the topmost rendered row from under someone who has scrolled up.
Pinned by `shell/src/__tests__/threadWindow.test.tsx`.

## What shipped 07-26 — after PR #58, direct on `master`

Four commits, all owner-reported from a running app rather than found by a gate.

- **`cc70ea8` — the icon pipeline was building onto a white plate.** QuickLook,
  the rasteriser both build scripts use, fits art top-left and pads the rest of
  the canvas with **white** (measured: 37px of 256), and flattens onto white — so
  every icon carried a white band down its right edge and along its bottom, and
  the app icon's rounded tile came back sitting in an opaque white square. Giving
  both masters a percentage size with an explicit `preserveAspectRatio` fixes it.
  The cause was under the whole pipeline, not in the artwork.
- **`e98828c` — the pointer glow is reverted.** Owner decision: it was
  decoration, and the accent is reserved for actions, selection and live state.
  Removed whole — component, stylesheet block, the mount in `App`, its five
  tests — so `styles.css` is byte-identical to before it landed and CLAUDE.md
  needs no exception written for it. The icon fixes from the same commit stay.
- **`3ab1159` — the widget rail said everything twice.** It draws pinned widgets
  *and* adds core-computed rows (the token meter, the connection list), with
  nothing stopping both from showing the same source: with the seeded
  "Connections" widget pinned the rail read Ollama, Anthropic, the meter, then
  Ollama and Anthropic again. Ambient rows now stand down for any source a
  **pinned** widget already shows — pinned only, because a widget in the
  collapsed tray is not on screen and the ambient row is then the only place
  those facts appear. Same commit fixed both model menus vanishing for a frame.
- **`07cc9ee` — the empty-state starfield is gone.** Reported as "a pixel or
  something akin to it" beside the greeting. It was not an artifact: it was the
  prototype's starfield, implemented as the exact radial-gradient stack from
  `prototype.html:72`, and the brief does ask for it. At 1280x800 it is five 1px
  dots over a 464x276 box, two of them landing on the type. At that density it
  never reads as a field — it reads as dust, or as a dead pixel next to a word.

## What shipped 07-26 — per-token streaming (`streaming-replies`)

Owner report: replies did not appear as they were written, they just appeared.
Two commits, and the first is a bug fix, not a prerequisite chore.

### `d2174c1` — the reveal never ran in production

`d8493e6` (dark v4 wave, above) added a whole-answer scramble reveal for the
stated reason that "the core does not stream". **It never executed on a real
turn**, and the reason is worth keeping written down because three comments and a
test all asserted the opposite premise:

- The core DID emit `conversation.streamChunk` — once per turn, with the entire
  finished answer, from `orchestrator.stream_to_frontend(response.text)`.
- `conversation.sendMessage`'s **result carries no answer text** (only
  `userMessageId` / `assistantMessageId` / `answeredWith`), so that one chunk was
  the sole delivery path for the reply. `extractFinalText` returned null.
- So `revealFinalText`'s guard (`!streamTextRef.current && finalText`) was false
  on both halves, and the engine `appendStreamedText` had just started was torn
  down by `runTurn`'s `finally` a frame later. One 15-character frame of noise,
  then the whole answer.

Fixed in `useTurn`: an engine still mid-resolve when the turn settles is animating
text that has fully arrived, so it is promoted to a reveal and its `onDone`
releases the overlay. Catching up **before** the turn settles stays a pause
between deltas and keeps the engine — dropping it would hand the next chunk a
fresh engine that re-animates the whole accumulated prefix from character zero.

### The second commit on the branch — real streaming, all four providers

`ModelProvider.send` gains `on_delta: DeltaSink | None`. A provider that streams
calls it per piece of prose and **still returns the same complete
`ModelResponse`**, so the tool loop, `usage_log` recording and the fallback chain
cannot tell the two paths apart — streaming stays a transport detail, and the
`effort` precedent (accept-and-ignore) covers every provider that can't do it.

Two rules bind every implementation, and the tests are built around them rather
than around "some deltas arrived":

1. **What is streamed equals what is returned.** Every provider test asserts the
   joined deltas against `response.text`.
2. **Only prose reaches the sink.** Anthropic `input_json_delta` and
   `thinking_delta`, OpenAI fragmented `function.arguments`, Google
   `functionCall` parts — all accumulated silently, never shown.

Shared primitives in `providers/base.py`: `open_stream` (uses
`client.send(request, stream=True)`, which composes with `request_with_retry`
unchanged — `idempotent=False` still retries only the connect-level failures that
prove nothing was generated or billed) and `iter_sse_json`.

Per-provider notes worth not rediscovering:

- **Ollama streams NDJSON, not SSE**, and its streaming is **conditional**: a
  model on the non-native **fallback** path returns tool calls as a fenced JSON
  block inside ordinary reply text, so streaming it would put
  `{"tool": "delete_file"}` on screen as though Addison had said it. Whether a
  piece of that text is prose or machinery is only knowable once the block closes,
  so `native or not tools` gates it. Not a limitation to remove later.
- **OpenAI needs `stream_options.include_usage`** or a streamed turn reports no
  tokens at all and §4.8's substrate silently loses it.
- **Google's `usageMetadata` is cumulative, not additive** — last frame wins.
  Summing frames would over-report every streamed turn into the token meter.
- **Setup Assistant relay accepts and ignores the sink**: its requests are SIGNED
  over the assembled body (§5), so a streaming variant is a change to the relay's
  contract, which lives outside this repo. Its answers arrive whole and get the
  frontend reveal.

**New honesty rule on the routed path.** Once a delta has been shown, a
`ProviderUnavailable` may **not** fall forward: the next candidate returns a
COMPLETE answer, which appends to the partial one and yields a single message that
reads as one answer and is two. Nothing can un-say what was shown, so the turn
fails with that provider's own sentence. Same reasoning as the existing
`committed` cross-provider forbid. A stream that dies **before** emitting anything
falls forward exactly as before — that case is tested too, so the rule cannot
quietly become "streaming disabled fallback".

**Visible side effect, deliberate:** a tool round's preamble text ("Let me look
at that…") now reaches the reader as it arrives, where previously only the final
answer was pushed. It appends to the same message.

`supports_streaming` on `ProviderCapabilities` is **declared and read by
nothing** — verified by grep. It was not repurposed as "honours `on_delta`",
because Ollama's answer to that is conditional; the reasoning lives at each
`send()` instead.

## Tracked thread: macOS keychain prompts

**STATUS 2026-07-25: root causes confirmed and FIXED. Merged as PR #57**
(`fix/keychain-double-prompt`, commit `5e435dd`). PR #58 and everything after it
sits on top of this.
The unexplained multi-prompt symptom was diagnosed by a multi-agent audit with
adversarial verification and fixed the same day. What it was, in one breath: the
device identity had no session cache (two OS reads per relay message —
`getDeviceKey` + `signRelayRequest`); a denied/failed provider-key read was
indistinguishable from "no key saved", so the core silently rerouted the turn to
the external Setup Assistant relay (which then raised the two device-identity
dialogs); and `keychain::handle` ran synchronously inside the stdout pump, so one
open dialog stalled every core↔shell call into the 60s bridge timeout. The owner's
"six prompts + app restarting on its own" episode was the dev watcher: agents
editing `keychain.rs` while `tauri dev` ran meant a rebuild+relaunch (fresh ad-hoc
signature, empty session cache) per save.

Shipped (all suites green: cargo 64, pytest 879, ruff, pyright):

- **Device-identity session cache** (`DEVICE_CACHE`, keychain.rs) — same owner
  decision as KEY_CACHE; one OS read per launch; corrupt blobs never cached.
- **The three-way key-read seam** — `{"key": "<value>"}` / `{"key": ""}` (nothing
  saved — a normal result now, NOT an error) / app error "Couldn't read your saved
  key from the keychain." Core-side: `_primary_key_status()` = ready | missing |
  unreadable (`rpc/constants.py`); **unreadable answers the turn on-machine with
  `_KEY_UNREADABLE_MESSAGE` in BOTH profiles — a keychain failure can no longer
  send a message to the relay** (pinned by `tests/test_keychain_read_failures.py`
  with a recording relay stub).
- **Negative failure cache** (`FAILED_READS`, keychain.rs) — a denied read is
  remembered for the session (stops the 60s stats-poll dialog storm); evicted on
  store/delete (re-saving or removing the key is the retry signal).
- **Pump unblocked** (`spawn_keychain_request`, agent_process.rs) — keychain work
  runs on a blocking task; OS access serialized by one `OS_KEYCHAIN` mutex (also
  taken by store/delete, which closes the parked-read stale-cache race);
  `_KEYCHAIN_TIMEOUT = 600s` core-side for the three keychain methods.
- **Migration ordering** — legacy entry deleted only after the copy lands
  (`migrate_legacy_key`, closure-testable); **Remove now also deletes the legacy
  `provider-key:primary`** so a removed key cannot resurrect (the orphan measured
  on the owner's machine was the live resurrection source).
- **provider.connect** reads the key up front so a read failure surfaces the
  keychain sentence, not "That key doesn't work."

**Round-2 regression fixes (2026-07-25, same working tree, all suites green:
cargo 66, pytest 880, ruff, tsc, clippy).** A second adversarial audit of the
round-1 diff found five real regressions it had introduced; all fixed:

- **Sync keychain Tauri commands froze the whole window.** `store_provider_key`
  / `delete_provider_key` took the new `OS_KEYCHAIN` mutex on the main thread, so
  a core-initiated read parked at a password dialog would beachball the UI. Both
  are now `async` + `spawn_blocking` (keychain.rs).
- **Frontend 120s timeout vs the core's 600s keychain wait.** `sendMessage` now
  uses `TURN_TIMEOUT_MS = 900_000` (client.ts) so the reply the person waited for
  behind a dialog isn't dropped and the composer can't invite a duplicate turn.
- **Anthropic-only probe rerouted OpenAI/Google/custom-only users to the relay.**
  `_run_send_message` now treats a connected non-Anthropic provider row as
  standing evidence of a PRIMARY-capable setup (`_other_cloud_provider_connected`,
  rpc/conversation.py) — no wrongful off-machine detour, no false BYOK demand.
- **Detached keychain task could answer a respawned core's colliding id.** The
  core channel now carries a `generation` counter (agent_process.rs); a keychain
  task writes back only while its captured generation still matches, else drops.
- **`delete_provider_key` evicted the cache before taking the OS lock**, so a
  dialog-parked read could resurrect the removed key. It now evicts again *under*
  the lock.
- Also: a per-turn `fresh` read (`get_provider_key(provider, fresh)`, keychain.rs;
  `_primary_key_turn_probe`, main.py) lets a person's own next message retry past
  a dismissed dialog, while the launch/poll probes stay quiet — the negative
  cache no longer strands a user who dismissed once by mistake.

Still open (all LOW severity, from the round-2 audit — verify before acting, the
verifier agents were cut off by a session limit): `shell.pickDirectory` still
keeps the 60s budget though it waits on a person; connect-card vs chat-turn use
two different "unreadable" sentences; `_KEY_UNREADABLE_MESSAGE` names a permission
prompt that (for the automatic pollers) the negative cache suppresses; the
`answeredWith routed` flag mislabels an explicitly-chosen Local turn as "answered
with a free model"; net-vetting gaps (`read_web_page` omits `total_timeout`; the
custom-provider CHAT `send()` re-resolves its base_url unpinned; connect-validation
GET reads the body with no size cap); snapshot-floor edges (`restore_last_working`
gives up on first apply failure unlike the sidecar arm; a G4 anchor duplicating
the newest verified fingerprint can defeat the newest-two-verified prune
exemption; `_sweep_sidecars` can erase prior sidecar history once a fresh DB has
one genesis row); `undo_manager.record()` sits outside the per-call error
envelope; `_canonical`'s unconditional casefold can merge distinct dirs on a
case-sensitive volume (widens workspace trust); the file tools' permission card
names the raw arg basename, not the resolved path. A failed *device-identity*
read is still not negative-cached (aborts the turn visibly, no storm — deliberate);
the wall-clock upper-bound assert in `test_shell_bridge.py` may flake under load;
signing-script automation is still un-agreed (ask before imposing the split dev
loop) *(answered 2026-07-31 — `sign-and-run.sh` signs from the cargo runner seam,
so there is no split dev loop to impose; see the entry below)*.

**STATUS 2026-07-24: step 1 of the plan is DONE and working.** *(Superseded
2026-07-31 by `sign-and-run.sh`, which does this as a cargo runner and adds the
explicit designated requirement — read the rest of this block as the record of
07-24, not as instructions. [CONVENTIONS.md](CONVENTIONS.md) owns the live
mechanism.)*
`scripts/sign-dev-binary.sh` signs the dev binary with a stable self-signed
certificate. Verified on the owner's machine — the designated requirement the
keychain ACL matches on went from a per-build hash to:

```
designated => identifier addison and certificate leaf = H"c24af4b8…"
```

Two things the next session needs to know:

- **The certificate must be TRUSTED, not just created.** A self-signed root from
  Certificate Assistant is `CSSMERR_TP_NOT_TRUSTED` until you open it in Keychain
  Access → Trust → set **Code Signing** to **Always Trust**. Until then
  `security find-identity -v -p codesigning` reports **0 valid identities** and the
  script correctly refuses. This step was missing from the first version of the
  instructions and is where the owner got stuck; the script now detects that exact
  state and says so. (That fix merged as PR #50.)
- **`cargo` strips the signature on every rebuild**, so `./scripts/sign-dev-binary.sh`
  must be re-run after each build *(no longer: `sign-and-run.sh` does it, 07-31)*.
  This is a step someone will forget. Wiring it
  into the dev loop was offered and **not** done, because `tauri dev` builds and
  runs in one step with no hook between, so automating it means running Vite and
  the binary separately — a workflow change the owner has not agreed to. Ask before
  imposing it. *(Answered 07-31: the cargo **runner** seam was the missing hook, and
  `sign-and-run.sh` uses it — no split dev loop, no manual re-run.)*

**The multi-prompt symptom was the unexplained one, and the 07-25 audit above
explained it.** The owner reported **three prompts in a single launch** (one
process, confirmed by `ps`). Signing explained prompts *across* rebuilds but not
three within one process, because `KEY_CACHE` should collapse provider-key reads
to one. The hypothesis recorded here was that a failing `provider-key:anthropic`
read fell through to the Setup Assistant relay, which then read `device-identity`
twice with no cache — three prompts naming **three different items**. That is
what the audit found and fixed: the three-way key-read seam (a failed read is now
distinguishable from "nothing saved" and answers on-machine instead of routing to
the relay), `DEVICE_CACHE`, and the orphaned legacy entry as the resurrection
source. **The diagnostic stays cheap if it ever recurs: macOS names the item in
the dialog.** Same name three times = three launches, benign. Three different
names = the cascade is back.

Original diagnosis below. Cause 1 is unchanged; cause 2 has since been fixed
(`DEVICE_CACHE`) and is kept for the reasoning.

**1. Dev builds are ad-hoc signed, so the ACL is invalidated on every rebuild.**
`codesign -dv` on `shell/src-tauri/target/debug/addison` reports
`Signature=adhoc`, `TeamIdentifier=not set`, and an identifier carrying a
per-build hash (`addison-<hash>`, not the `app.addison.desktop` in
`tauri.conf.json`). macOS keys the "Always Allow" keychain ACL to the signing
identity, so **every rebuild presents itself as a different application** and the
ACL is discarded. Clicking Always Allow in development is therefore not sticky,
and never can be while the signature is ad-hoc.

**2. `ensure_device_keypair()` is not covered by `KEY_CACHE`. — FIXED 2026-07-25**
by `DEVICE_CACHE` (keychain.rs), one OS read per launch. `KEY_CACHE`
(`keychain.rs`) caches *provider* keys, and `get_provider_key` consults it first —
one OS read per provider per launch. `ensure_device_keypair` calls
`entry.get_password()` directly with no cache, so the Setup Assistant relay path
does **one OS keychain read per message**. On a build whose ACL keeps being
invalidated, that is one prompt per message.

**A trace exists now (2026-07-31).** `ADDISON_KEYCHAIN_TRACE=1` prints every
keychain touch from BOTH processes to the one stderr the dev terminal shows:
`keychain.rs` prints the OS touches (the thing that costs a dialog) and
`shell_bridge.py` prints the core call site (which the shell cannot see). Off by
default in every build — a keychain trace on by default is a keychain trace in
someone's support-log paste — and it never carries key material: `KeyRead::Found`
renders as the bare word `found`, never the value, its length, or a prefix
(`a_trace_line_never_carries_key_material`).

**What the owner reported on 07-31, and why it narrows things:** two dialogs on
launch, **naming the same item**, where pressing *Allow* still raised the second
and *Always Allow* stopped it. That is diagnostic: **"Allow" is a single-ACCESS
grant** — it records nothing — while "Always Allow" writes an ACL onto the item.
So two dialogs for one item means **two separate OS reads**, which `KEY_CACHE` and
`OS_KEYCHAIN` were built to prevent. A read that returns `Found` is cached and
cannot prompt twice, so the first read is not returning a key; the two candidates
are a remembered `Unreadable` that a `fresh` per-turn probe deliberately retries
past, and `NothingSaved`, **which is never cached at all** on the reasoning that
"nothing saved costs no dialog". The trace distinguishes them in one launch.

**RESOLVED 2026-07-31 — the trace settled it, and step 1 shipped.** A live trace
of two launches showed exactly ONE `OS-TOUCH` on `provider-key:anthropic`, and the
read did not return until BOTH dialogs were answered (Esc on the second aborted it
to `unreadable`). So the two prompts were never two reads: they are two ACL
authorizations for a single `SecKeychainFindGenericPassword`, against an app
identity macOS cannot match. Everything on Addison's side was ruled out first —
`keyring` 3.6.3 makes one `find_generic_password` call per read, there are no
duplicate keychain items (an earlier check used `sort -u`, which would have hidden
them), and the login keychain is `no-timeout` so the first dialog was not an
unlock. Cause 1 below was the whole story.

**Two things the same trace established, worth keeping:**

- **A dismissed dialog costs the whole session.** Esc on launch left every later
  read answering `unreadable (no OS touch)` from `FAILED_READS` for 74 seconds —
  the app ran keyless, and the UI said "not connected" with no explanation.
  Recovery works exactly as designed: the first user MESSAGE probes with
  `fresh=true`, re-reads, and finds the key. Worth surfacing in the UI rather than
  leaving the person to guess.
- **`nothing-saved` is never cached, so it re-reads forever.** OpenAI and Google
  are read from the OS on every 60-second `stats.get` poll, permanently. Free in
  dialogs today (no item exists, so there is nothing to authorize) — but it is the
  one read path with no memory in front of it, and it is the shape the original
  three-prompt cascade had.

**Agreed plan, in order — do not skip to the end:**

1. **A stable self-signed development certificate. — SHIPPED 2026-07-31.**
   `shell/src-tauri/sign-and-run.sh` + `shell/src-tauri/.cargo/config.toml`: a
   cargo **runner** signs each dev
   build with the `Addison Dev` identity and then execs it. The runner is the only
   available seam — `npm run tauri dev` builds and launches in one step,
   `beforeDevCommand` runs before the Rust build, and `bundle.macOS.signingIdentity`
   applies to `tauri build` only. Identifier goes from `addison-<per-build-hash>`
   (adhoc) to a stable `addison`, so one "Always Allow" survives every later
   rebuild. Fails OPEN on a machine without the identity (warns, runs unsigned):
   it is a convenience wrapper, not a security control, and a fresh clone must
   still build. Scoped to the app binary so `cargo test` is untouched, and
   `ADDISON_SIGN_ONLY=1` signs without launching, because a script whose normal
   path ends in `exec` into a GUI window is otherwise untestable.
   Fixes the actual cause: a stable signing identity means the ACL survives
   rebuilds and Always Allow works. Free. *The $99 Apple Developer Program is for distribution — signing,
   notarisation, shipping to other people's machines. It is a Phase-3 packaging
   concern and buying it now would not fix this.*
2. **Cache the parsed `SigningKey`** in the same shape as `KEY_CACHE`, only if
   prompts persist after (1). Deliberately second: it is a workaround for
   per-message reads, and it widens what sits in process memory, so it should not
   be spent on a problem step 1 may have already solved.
3. **Secure-Enclave-backed device identity — Phase 3.** Note the constraint
   before planning around it: the Secure Enclave is **ECDSA P-256 only, not
   ed25519**. Today's identity is ed25519 (`ed25519_dalek`, deterministic per RFC
   8032), so this **changes the relay signing contract** on both ends. It is a
   protocol change, not a storage change.

**Also found: an orphaned legacy keychain entry.** `get_provider_key` migrates
`provider-key:primary` into `provider-key:anthropic` and best-effort deletes the
legacy entry — but only on a read that finds **no** per-provider entry. Once
`provider-key:anthropic` exists, the read returns early and the legacy account is
never revisited. So a legacy entry orphans permanently whenever the migration's
best-effort delete failed, or whenever the user saved an Anthropic key under the
new scheme before any read triggered the migration. `delete_provider_key`
("Remove") then deleted only the per-provider account, so a stale key sat in the
user's keychain after they believed they had deleted it — and the next read
resurrected it through the migration fallback. **FIXED 2026-07-25:**
`delete_provider_key_blocking` now deletes `provider-key:primary` as well when
the provider is the legacy one; a missing legacy entry counts as success, a real
failure is reported because the key genuinely is still on the machine, and the
retry is idempotent.

## The step-1 ledger retirement (2026-07-24)

**The step-1 ledger is RETIRED (2026-07-24).** The two pre-step-2 items shipped
on the `retire-step1-ledger` branch, each with mutation-proven tests:

- **`snapshot_now`** — shipped as a **LOW, capture-only** registry tool
  (`agent_core/tools/snapshot_now.py`), in `_V1_TOOL_IDS` so the companion gets
  it. Late-bound `Callable[[], SnapshotManager | None]` wired through
  `build_registry()` + a holder filled after server construction in `main()`;
  answers *"I can't save a restore point just yet"* before the store is up; a
  successful save clears the sticky capture-failure warning, matching the
  Settings button. An AST source test forbids every manager verb except
  `capture` in the module. `docs/architecture.md` / `docs/classes.md` now
  describe the shipped tool.
- **The Restore card says so when there is NO verified restore point**
  (`SnapshotsCard.tsx`): two client-derived sentences — no row verified (G3
  silently off until the first completed turn) vs. some row verified but no
  target (everything saved matches the running config). The core's
  'unreadable' walk outcome is indistinguishable from 'identical' on this wire
  — accepted, commented in the component; the wire's `why` field is the future
  fix if that distinction ever has to be drawn. (Salvaged from the scrapped
  "doctor command" — see git history for why doctor contradicted G3.)
