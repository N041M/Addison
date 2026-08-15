# Known bugs — from the whole-app test pass (8–9 Aug 2026)

Working fix-list distilled from the QA artifact ("Addison — whole-app test pass",
baseline `7733dbb`). Each item names a repro and, where known, the code area.
When one is fixed, re-run its red-railed check in the artifact and only then
strike it here. Live *design* questions stay in `docs/KNOWN-GAPS.md` — this file
is only defects with a known wrong behaviour.

## P1 — broken features

1. **Arming an automation from chat can never succeed — the id is never in the
   conversation, in any flow.** Root cause pinned 2026-08-11: `create_automation`'s
   tool result (`_result_text`, `create_automation.py:501`) hands the model the
   schedule sentence, the plist preview and the not-armed line but **never
   `row.id`**; the plist carries only the label, which arming doesn't accept.
   `arm_automation` resolves strictly by UUID (`arm_automation.py:307`), so even
   create-then-"arm Probe" *in the same conversation* fails (reproduced
   2026-08-11), and the refusal lies: `_NO_SUCH_AUTOMATION` says the automation
   "isn't saved any more" while the row sits in the store. The model's natural
   recovery — "recreate and arm in one go" — also dead-ends on the duplicate-name
   guard (`_NAME_IN_USE`). This subsumes the earlier Settings-"Arm…" finding: the
   seeded name-only sentence is just one instance of the same missing id.
   *Fix, in order: (a) surface `row.id` in `_result_text`; (b) make the refusal
   distinguish "no row with that id" from "that isn't an id" and never claim
   deletion; (c) optionally let `arm_automation` resolve a unique name, refusing
   on ambiguity — which is also what the Settings seeded sentence needs.*
   `agent_core/tools/create_automation.py` · `agent_core/tools/arm_automation.py` ·
   SettingsPage AutomationsSection · artifact §07

2. **Gemini 3.x multi-step tool turns always fail.** The adapter does not replay
   Gemini's `thought_signature` on the second model call, every multi-step tool
   turn 400s, and the error is surfaced as "That key doesn't work" (it does — it
   was a 400, not an auth failure). Re-reproduced 2026-08-11 08:31:06 running the
   artifact §02 check (the Probe automation prompt): the turn died on the second
   call with `ProviderRequestRejected("That key doesn't work. Check it and try
   again.")` — same misattribution, still open.
   `agent_core/providers/` google adapter · artifact §02

## P2 — trust and lifecycle

3. **A pending approval card can stay invisible behind "Working…".** Observed
   ~4 minutes unrendered; it appeared only after Stop. Nothing ever times out.
   Frontend card rendering / turn state · artifact §04

4. **An approval card outlives its stopped turn and stays fully actionable.**
   Decide the intended behaviour (card dies with the turn, or survives
   explicitly) and enforce it. Gate ↔ turn lifecycle · artifact §04

5. **"Save as routine" is lost on conversation reload.** The work panel and the
   save link vanish after quit/relaunch; the steps become silently unsaveable.
   Frontend work-panel state · artifact §06

6. **Simple cannot edit existing files at all** — it refuses and offers only
   "save a new file" (observed 2026-08-11 running the §03 check; the model's
   refusal was accurate to the tool view). **Owner decision 2026-08-11: this is a
   bug — Simple should show the permission card first and then do the edit,**
   not lack the capability. Until 2026-08-11 `write_project_file` registered
   `open_only` and was absent from the SAFE view; PR #101 surfaced both
   path-bounded file tools in SAFE (carded per invocation, MEDIUM, real
   `undo()`), amending the SAFE-invariant wording and the workspace-trust pin in
   the same commit — docs/SAFETY.md invariant 1 owns the decision and its terms.
   Trusted-folder confinement and the size/symlink floors apply unchanged.
   The follow-up recorded in SAFETY.md is DECIDED and BUILT (owner decision
   2026-08-12): Simple has the "Folders Addison may work in" panel, same two-step
   ceremony, with copy that says what SAFE actually does — so the capability no
   longer reaches only folders trusted while Developer was active. Remaining
   before this is struck: the artifact §03 red-railed check re-runs green.
   `agent_core/tools/write_project_file.py` · `agent_core/tools/registry.py` · artifact §03

## P3 — quality

7. **Message segments fuse without whitespace** ("for you.The answer is…").
   Confirmed 9 Aug (second session): the fused text is also in the *settled*
   transcript ("file.Now I'll add", "the end:Done.") — this is not just the
   streaming renderer; the joined content is what gets persisted/rendered after
   the turn completes. Frontend message renderer (and possibly segment storage) · artifact §04

8. **Appends drop the trailing newline.** Addison writes the appended line
   without `\n`, so the next writer's line fuses onto it
   (`edited: yesmy own edit`). File-edit tool append path · artifact §09

9. **Changes entries carry no timestamp.** The Code screen's Changes list is
   supposed to show name and time; only the name renders. Code screen Changes list · artifact §09

10. **Revert confirm contradicts enforcement on swapped files.** When a file has
   been replaced (symlink/hard link/FIFO), the first put-it-back press still
   shows the generic "you've changed this file" confirm and offers a revert the
   engine then (correctly) refuses. The confirm should detect and say it first.
   Code screen revert confirm · artifact §10

11. **Routine plan capture is conversation-scoped.** "Save as routine" offers
    every tool call in the conversation, including stale unrelated steps, not
    the turn that produced the answer. Routine plan capture · artifact §06

12. **"Technical details" adds nothing.** The fold shows the same sentence
    wrapped in an exception class — no provider, no status code. Error surface · artifact §02

13. **Status footer claims before it knows.** Asserts "Simple profile · local"
    at launch before the engine has answered, then corrects itself. Frontend status bar · artifact §01

14. **One thing, two names.** Sidebar says "Snapshots", Settings says "Restore
    points". Naming, sidebar + SettingsPage.

15. **Mermaid diagrams render unthemed and clipped.** Observed 2026-08-11 (light
    theme, §04 check): nodes are solid near-black fills with dark text —
    black-on-black, illegible in the light theme and not Addison's palette in
    either theme; node labels truncate mid-word ("Task reques", "Do it dire",
    "Just reading or answ"); edge arrows render as oversized filled blobs rather
    than lines. The diagram itself is structurally correct — this is theming
    (mermaid theme variables not wired to the app's tokens) plus a node-sizing
    bug (fixed-width nodes clipping their labels).
    Frontend mermaid renderer / theme wiring · artifact §04

## Open questions (need a decision or one more observation, not yet a defect)

- **Cost-first vs. explicit model pick:** Opus answered under Cost first with no
  explanation — which wins, and where does the UI say so? artifact §02
- **Free-model disclaimer:** gemini-3.5-flash on a free-tier key answered with
  no "answered with a free model" note — trigger narrower than the spec's
  sentence, or missing? artifact §02
- ~~**A routine's answer never reaches the person:** Quick Sums reports only
  "Done — every step finished"; 6016 appears nowhere. artifact §06~~
  **DECIDED 2026-08-12 (owner).** A run now says what it is doing while it does
  it, and hands back what it produced. The engine emits one `routine.stepUpdate`
  per step as it begins and again when it ends (tool IDS — the RPC layer labels
  them from the registry, so a routine's steps read exactly like the chat panel's);
  `routine.run` carries `answer`, the last text the run produced. The Settings
  routine row expands a small panel under itself in the "Addison's work" idiom —
  live steps, a step waiting on a permission card saying so, a failed step naming
  itself in a plain sentence, and the answer as readable text at the end.
- **Diagnostics entries seemed profile-scoped** — confirm and decide. artifact §01
