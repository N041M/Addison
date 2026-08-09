# Known bugs — from the whole-app test pass (8–9 Aug 2026)

Working fix-list distilled from the QA artifact ("Addison — whole-app test pass",
baseline `7733dbb`). Each item names a repro and, where known, the code area.
When one is fixed, re-run its red-railed check in the artifact and only then
strike it here. Live *design* questions stay in `docs/KNOWN-GAPS.md` — this file
is only defects with a known wrong behaviour.

## P1 — broken features

1. **Settings "Arm…" can never succeed.** Only three automation tools exist and
   `arm_automation` resolves by UUID, but the seeded sentence the Arm… button
   drops into a fresh chat carries only the *name* — so the model has nothing to
   resolve and the refusal wrongly tells the user their automation "isn't saved
   any more" while the row sits in Settings. Arming works only when the id is
   already in the conversation.
   *Fix direction: either a list/lookup tool, or seed the sentence with the id.*
   `agent_core/tools/arm_automation.py` · SettingsPage AutomationsSection · artifact §07

2. **Gemini 3.x multi-step tool turns always fail.** The adapter does not replay
   Gemini's `thought_signature` on the second model call, every multi-step tool
   turn 400s, and the error is surfaced as "That key doesn't work" (it does — it
   was a 400, not an auth failure).
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

## P3 — quality

6. **Message segments fuse without whitespace** ("for you.The answer is…").
   Confirmed 9 Aug (second session): the fused text is also in the *settled*
   transcript ("file.Now I'll add", "the end:Done.") — this is not just the
   streaming renderer; the joined content is what gets persisted/rendered after
   the turn completes. Frontend message renderer (and possibly segment storage) · artifact §04

7. **Appends drop the trailing newline.** Addison writes the appended line
   without `\n`, so the next writer's line fuses onto it
   (`edited: yesmy own edit`). File-edit tool append path · artifact §09

8. **Changes entries carry no timestamp.** The Code screen's Changes list is
   supposed to show name and time; only the name renders. Code screen Changes list · artifact §09

9. **Revert confirm contradicts enforcement on swapped files.** When a file has
   been replaced (symlink/hard link/FIFO), the first put-it-back press still
   shows the generic "you've changed this file" confirm and offers a revert the
   engine then (correctly) refuses. The confirm should detect and say it first.
   Code screen revert confirm · artifact §10

10. **Routine plan capture is conversation-scoped.** "Save as routine" offers
    every tool call in the conversation, including stale unrelated steps, not
    the turn that produced the answer. Routine plan capture · artifact §06

11. **"Technical details" adds nothing.** The fold shows the same sentence
    wrapped in an exception class — no provider, no status code. Error surface · artifact §02

12. **Status footer claims before it knows.** Asserts "Simple profile · local"
    at launch before the engine has answered, then corrects itself. Frontend status bar · artifact §01

13. **One thing, two names.** Sidebar says "Snapshots", Settings says "Restore
    points". Naming, sidebar + SettingsPage.

## Open questions (need a decision or one more observation, not yet a defect)

- **Cost-first vs. explicit model pick:** Opus answered under Cost first with no
  explanation — which wins, and where does the UI say so? artifact §02
- **Free-model disclaimer:** gemini-3.5-flash on a free-tier key answered with
  no "answered with a free model" note — trigger narrower than the spec's
  sentence, or missing? artifact §02
- **A routine's answer never reaches the person:** Quick Sums reports only
  "Done — every step finished"; 6016 appears nowhere. artifact §06
- **Diagnostics entries seemed profile-scoped** — confirm and decide. artifact §01
