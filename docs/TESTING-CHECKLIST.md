# Addison v1 — Manual Desktop Testing Pass

Structured pass over every subsystem, one numbered step each. Run the app with
`cd shell && npm run tauri dev` and keep that terminal visible — the engine's
stderr lands there, and it's the first thing to copy when something misbehaves.

How to report an issue: step number + screenshot + any stderr lines from the
launch terminal. Findings get converted into automated coverage afterwards.

Cheap-model tip: export `ADDISON_MODEL=claude-haiku-4-5` before launching if
you want the whole pass to cost pennies; steps 9–10 exercise the picker anyway.

**Known gaps — not bugs, don't file them** (HANDOFF.md "Known gaps"):
drafting a message reports "not available yet"; there is no file-attach/drop
UI, so `read_file` is unreachable from chat; the Setup Assistant relay has no
server in this repo.

---

## 1. Launch & connect

**Do:** `cd shell && npm run tauri dev`. Wait for the window.
**Expect:** brief "Getting ready…" state, then the message box becomes active
with the placeholder "Tell Addison what you'd like help with…". No error
banner. Window looks calm/utilitarian — sharp corners, one steel-blue accent.
**Fail signs:** stuck on "Getting ready", or "Addison's engine isn't connected
yet." after ~10 s → copy the launch-terminal stderr.

## 2. Plain chat

**Do:** ask something tool-free, e.g. "What's a good way to remember names?"
**Expect:** a streamed reply in plain language. No permission card, no
activity panel. A second message keeps context from the first.

## 3. Permission card (calculator) — both answers

**Do:** ask "What's 18 % tip on 4 350 CZK?" When the card titled
**"Addison is asking"** appears, click **Not now** first.
**Expect:** the tool does not run; Addison acknowledges gracefully and either
answers without the tool or explains, without re-prompting in a loop.
**Do:** ask again; this time click **Allow**.
**Expect:** the answer arrives; the activity panel offers
**"Show what Addison did"** and the step list mentions the calculation.
No risk codes, tool ids, or jargon anywhere on the card.

## 4. Web search

**Do:** ask something that needs today's information (e.g. "What's the weather
in Prague right now?" or a current-events question).
**Expect:** permission card → Allow → **"Searching the web…"** in the activity
panel, then an answer grounded in the results. Check the expanded steps name
the search plainly.

## 5. Save a file (native dialog)

**Do:** "Write a two-line thank-you note and save it as a file."
**Expect:** permission card → Allow → the **native macOS save dialog** opens
(this is the Rust shell, not a web dialog). Save into a scratch folder.
Verify in Finder the file exists with the right content.

## 6. Undo

**Do:** immediately after step 5, click **"Undo last action"** (activity
panel / rewind controls).
**Expect:** the saved file is really gone from Finder, and Addison confirms in
plain words ("Put things back the way they were." style — no stack trace).
**Also:** try undo when there's nothing meaningful to undo — expect a calm
"Couldn't undo that."-class message, not an error dump.

## 7. Rewind

**Do:** hover an earlier message of yours and use **"Rewind to here"**.
**Expect (edit-and-resend semantics):** that message and everything after it
leave the conversation, and its text lands back in the message box for
editing. Nothing re-runs until you press Send. A follow-up message continues
from the rewound state (later context is genuinely forgotten).

## 8. Routines — propose, run, remove

**Do:** give a small multi-step task (e.g. steps 3+5 combined: "calculate X
and save the result as a file"). After it finishes, look in the **activity
strip just below the conversation** ("Finished the steps below") for the small
link **"Save these steps as a routine"** — click it, and the confirmation card
**"Save these steps as a routine?"** appears → click **Save routine**.
**Do:** open Settings → **Routines** → your routine → **Run now**.
**Expect:** it replays with **zero model calls and zero permission
re-prompts**, ends with "Done — every step finished." The saved file from the
routine run exists. **"View plan"** shows the declarative steps (in Developer
profile; see step 11).
**Do:** click **Remove** → expect the **"Really remove?"** confirmation, then
the routine is gone from the list.

## 9. Model picker + effort levels

**Do:** open the picker by the message box.
**Expect:** the dynamic model list from your key (raw API names, e.g.
"Claude Opus 4.8", "Claude Sonnet 5", "Claude Haiku 4.5"), and
**"How thorough Addison should be"** effort options only on models whose API
capabilities support them.
**Do:** send one message on a non-default model.
**Expect:** that message uses the picked model (per-message choice), and the
next message falls back to the default. Then in Settings →
**"Where Addison thinks"**, change the default **Cloud model** and confirm the
picker reflects it.

## 10. Profiles — surface changes only

**Do:** Settings → **Profile** (last section). Switch Simple → Developer.
**Expect:** the change applies instantly — technical affordances appear
(e.g. "Technical details" on messages, routine "View plan") — and **persists
across an app restart**.
**Critical check:** repeat step 3 in each profile. The permission card must
appear in *both* profiles for the same action — a profile never changes what
Addison asks permission for, only what it shows.
**Do:** switch back to Simple; confirm developer affordances disappear.

## 11. Local models (only if Ollama is installed)

**Do:** Settings → **"Run a model on this computer"**.
**Expect:** three plain-language options ("Light and quick" / "Balanced" /
"Most capable") with honest size + memory requirements. Set one up (or, if
already set up, confirm **"On this computer"** appears in the model picker)
and send a message through it.
**Skip** if Ollama isn't installed — but then confirm the section explains
itself plainly ("What's Ollama?") rather than erroring.

## 12. Engine loss & recovery (resilience)

**Do:** with the app running, kill the Python engine process
(`pkill -f agent_core.main`).
**Expect (by design):** the shell announces "Addison's engine stopped —
restarting…", respawns it ONCE, then shows "Addison's engine restarted — you
can keep chatting." Chat and the model picker must work normally afterwards
(the app re-fetches the model list from the new engine). A second kill stays
down: "Addison's engine has stopped. Please restart the app."

## 13. Dark restyle & rich rendering (visual, amended 2026-07)

**Dark theme.** On launch, the window shows **no white flash** before the CSS
loads (the body pre-paints dark), the macOS **titlebar is dark** ("theme": "Dark"),
and the overall look is dark, calm, and legible — not neon. Scrollbars in the
thread read on dark; keyboard focus rings (Tab through buttons/inputs) are clearly
visible. The wordmark, uppercase sender labels, activity/tool labels, the model
name, timestamps, and the composer hint render in **monospace**; message body text
stays sans.

**History.** Open **History** — the list renders, rows hover-highlight, and the
current conversation is marked. Reopen a past conversation (it loads back into the
thread) and start a **New chat** (thread resets to the welcome message).

**Markdown.** Ask for an answer using **bold**, a bulleted and a numbered list, a
table, and a fenced code block in some language — confirm the code is
syntax-highlighted with the calm (non-neon) palette and reads against the inset
code background.

**Mermaid.** Ask for a small diagram (e.g. a flowchart) — it renders as a dark-
themed SVG that matches the app. Then send a **malformed** ```mermaid fence and
confirm it **falls back** to plain code rather than breaking the message row.

---

## After the pass

For each failed step: screenshot + stderr → diagnose → fix → **add an
automated test that would have caught it** (pytest for core behavior, the
live-driver pattern in HANDOFF.md for end-to-end, cargo tests for shell
behavior). Then the UI/UX polish phase starts, fed by whatever this pass
surfaced.
