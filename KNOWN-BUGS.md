# Known bugs — from the whole-app test pass (8–9 Aug 2026)

Working fix-list distilled from the QA artifact ("Addison — whole-app test pass",
baseline `7733dbb`). Each item names a repro and, where known, the code area.
When one is fixed, re-run its red-railed check in the artifact and only then
strike it here. Live *design* questions stay in `docs/KNOWN-GAPS.md` — this file
is only defects with a known wrong behaviour.

**Re-run pass 2026-08-21** (current master `0997410`, debug bundle + live app,
remote-driven + owner-driven): **all fifteen entries re-ran green** and are
struck below, each with what the re-run actually showed. The same session also
ran the artifact's never-run sections — the floors, Custom + the G4 anchor, C6,
the Code screen's restart/theme/width checks, and both engine kills — all
green; the artifact records each. It also found the environment trap the
artifact's first panel warns about, twice over: a stale Aug-9 debug bundle
answering under the current frontend, and one **new cosmetic finding** — the
"Addison's work" panel double-lists a step while the turn streams (one
`calculator` audit row, two identical bullets); the reloaded transcript shows
it once, so persistence is correct.

## P1 — broken features

1. ~~**Arming an automation from chat can never succeed — the id is never in the
   conversation, in any flow.**~~ **RE-RUN GREEN 2026-08-21.** Both dead flows
   work: create-then-"arm Heartbeat" in one conversation reached the full
   keyword card (fresh nonce; `DJH-WH9`), and the Settings "Arm…" seeded
   sentence reached the same card with a different fresh nonce (`7QU-HVT`).
   A real end-to-end arm ran the same evening on a Gemini turn: create → card →
   typed code → plist written → Remove disarmed and deleted it, `launchctl`
   clean, no first run (G2 held). Root cause had been pinned 2026-08-11:
   `create_automation`'s result text never carried `row.id`, and
   `arm_automation` resolved strictly by UUID; fixed by PR #98 (id surfaced,
   honest refusal, unique-name resolution).
   `agent_core/tools/create_automation.py` · `agent_core/tools/arm_automation.py` ·
   SettingsPage AutomationsSection · artifact §07

2. ~~**Gemini 3.x multi-step tool turns always fail.**~~ **RE-RUN GREEN
   2026-08-21, live.** `gemini-3.5-flash` on the owner's key completed the
   artifact's own §02 check (the Probe automation prompt): two tool calls, a
   permission card answered mid-turn, and a closing sentence. No 400, no
   misattributed "That key doesn't work". Fixed by PR #109 (the adapter replays
   `thought_signature`).
   `agent_core/providers/` google adapter · artifact §02

## P2 — trust and lifecycle

3. ~~**A pending approval card can stay invisible behind "Working…".**~~
   **RE-RUN GREEN 2026-08-21** (owner-driven): a destructive-command card left
   deliberately unanswered stayed rendered the whole wait, every card of ~12
   other turns rendered the moment the engine asked, and the shipped behaviour
   goes further than the fix promised — navigating away while a card waits is
   held, with a plain banner ("Answer Addison's question first — it's still
   waiting for you."), which makes this failure class structurally
   unreachable. (PR #110: below-the-fold diagnosis + watchdog.)
   Frontend card rendering / turn state · artifact §04

4. ~~**An approval card outlives its stopped turn and stays fully actionable.**~~
   **RE-RUN GREEN 2026-08-21.** Stop pressed with a "Run a command" card
   pending: the reply settled to "(Stopped.)" and the card's controls were
   replaced by "This request ended when you stopped the answer." Decided and
   enforced as card-dies-with-the-turn (PR #99, plus #110's interaction fix).
   Gate ↔ turn lifecycle · artifact §04

5. ~~**"Save as routine" is lost on conversation reload.**~~ **RE-RUN GREEN
   2026-08-21.** Quit + relaunch + reopen: the work panel and the "Save as
   routine" link both survive, and the plan offered after reload is the same
   turn-scoped plan. (Fixed by PR #100.)
   Frontend work-panel state · artifact §06

6. ~~**Simple cannot edit existing files at all.**~~ **RE-RUN GREEN 2026-08-21**
   on the current build: in Simple, an append to an existing file produced the
   read card, then the edit card naming the file ("It wants to change the file
   “Notes.md”. You can undo this afterwards."), then the edit — per invocation,
   with undo. PR #101 surfaced both path-bounded file tools in SAFE; the
   folder-trust follow-up (owner decision 2026-08-12) is built and was seen
   live ("Folders Addison may work in" panel in Simple).
   `agent_core/tools/write_project_file.py` · `agent_core/tools/registry.py` · artifact §03

## P3 — quality

7. ~~**Message segments fuse without whitespace** ("for you.The answer is…").~~
   **RE-RUN GREEN 2026-08-21.** Zero fused joints across the whole day —
   ~12 remote-driven turns plus the owner's own session, streaming and settled
   both. (PR #102. The one fused line seen all day was `echo >>` onto a file
   with no trailing newline — the shell writing exactly what an approved
   command said, out of scope by design.)
   Frontend message renderer · artifact §04

8. ~~**Appends drop the trailing newline.**~~ **RE-RUN GREEN 2026-08-21.**
   `X0\n` + "add a line X1" landed as `X0\nX1\n` — the shell restored the
   dropped byte (`needs_trailing_newline`, PR #108), and the narrow rule held
   in the other direction: a file already ending without a newline was left
   exactly as sent. (The first re-run "failure" was a stale Aug-9 shell binary
   wearing the current frontend — see the artifact's first panel, which warned
   about precisely this.)
   File-edit tool append path · artifact §09

9. ~~**Changes entries carry no timestamp.**~~ **RE-RUN GREEN 2026-08-21.**
   Every row in the Code screen's Changes list shows name, edit count and time
   ("x.txt 16:54", "b.txt 2× 16:21"). (PR #103.)
   Code screen Changes list · artifact §09

10. ~~**Revert confirm contradicts enforcement on swapped files.**~~ **RE-RUN
    GREEN 2026-08-21.** With a hard link planted at `a.txt`: selecting the
    change already shows "A different file is at that name now, so this isn't
    the change Addison made", the confirm makes no false "you've changed this
    file" claim, and pressing through refuses with "…so Addison won't put the
    old text there. Nothing was changed." — nothing written through the link.
    (PR #108.)
    Code screen revert confirm · artifact §10

11. ~~**Routine plan capture is conversation-scoped.**~~ **RE-RUN GREEN
    2026-08-21.** In a conversation carrying earlier read/edit tool calls, a
    calculation's "Save as routine" offered exactly one step ("1. Do math and
    unit conversions"). (PR #104.)
    Routine plan capture · artifact §06

12. ~~**"Technical details" adds nothing.**~~ **RE-RUN GREEN 2026-08-21**
    (owner-driven, Wi-Fi off): the fold showed `provider: google ·
    gemma-4-31b-it` and `ProviderUnavailable(…)` — the provider and attempted
    model id, which the sentence did not carry. No status code, honestly: no
    HTTP happened. (PR #108.) The same screenshot is now the sharpest evidence
    on the cost-first-vs-explicit-pick open question below.
    Error surface · artifact §02

13. ~~**Status footer claims before it knows.**~~ **RE-RUN GREEN 2026-08-21**,
    two launches: the footer stays empty until the engine answers, then states
    the real profile. No premature "Simple profile · local". (PR #105.)
    Frontend status bar · artifact §01

14. ~~**One thing, two names.**~~ **RE-RUN GREEN 2026-08-21.** Sidebar row and
    Settings section both read "Restore points"; "snapshot" stays internal
    vocabulary (naming decision recorded in
    `docs/design-brief-dark/IMPLEMENTATION.md`). (PR #106.)
    Naming, sidebar + SettingsPage.

15. ~~**Mermaid diagrams render unthemed and clipped.**~~ **RE-RUN GREEN
    2026-08-21 in the light theme** — themed node fills, legible dark-on-light
    text, labels wrap without truncation, arrows are thin lines with proper
    heads (PR #107). The dark-theme glance is still to be taken; light was the
    red-railed observation.
    Frontend mermaid renderer / theme wiring · artifact §04

## Open questions (need a decision or one more observation, not yet a defect)

- **Cost-first vs. explicit model pick — evidence and an owner directive,
  2026-08-21.** With Claude Haiku 4.5 explicitly picked and strategy Cost
  first, an offline turn's Technical details showed the router attempting
  `provider: google · gemma-4-31b-it`: the explicit pick does not win, and
  nothing in the UI says which model actually answers. **Owner note
  (2026-08-21): the picker system reads as broken when this happens; the
  answering model should be disclosed on a tab/indicator next to the model
  picker.** Design work for a next wave; not a re-run item. artifact §02
- **Free-model disclaimer:** ~~gemini-3.5-flash on a free-tier key answered with
  no "answered with a free model" note.~~ **DECIDED AND FIXED 2026-08-15** (PR
  #113 part B): the chip now asks only `free`, known-free stays by
  construction (Ollama locals; cloud entries stay `false`).
- ~~**A routine's answer never reaches the person.**~~
  **DECIDED 2026-08-12 (owner).** A run now says what it is doing while it does
  it, and hands back what it produced. The engine emits one `routine.stepUpdate`
  per step as it begins and again when it ends (tool IDS — the RPC layer labels
  them from the registry, so a routine's steps read exactly like the chat panel's);
  `routine.run` carries `answer`, the last text the run produced. The Settings
  routine row expands a small panel under itself in the "Addison's work" idiom —
  live steps, a step waiting on a permission card saying so, a failed step naming
  itself in a plain sentence, and the answer as readable text at the end.
- **Diagnostics entries seemed profile-scoped** — confirm and decide. artifact §01
- **The work panel double-lists a step while the turn streams (new,
  2026-08-21).** One `calculator` call produced two identical "Do math and unit
  conversions" bullets live; the same panel after reload shows one. One audit
  row, so this is the streaming renderer, not dispatch. Cosmetic; worth a look
  the next time the work panel is open anyway. Frontend work-panel streaming.
