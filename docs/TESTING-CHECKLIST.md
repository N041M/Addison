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
banner. Window looks calm and warm: the paper-neutral Fern look, one
fern-green accent.
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

## 4a. Read a web page

**Do:** ask for something that needs the *contents* of a page, not a link — e.g.
"What does the front page of bbc.co.uk say right now?" or, after a search,
"read the first result and tell me what it says."
**Expect:** permission card → Allow → **"Read a web page"** in the activity
panel, then an answer that quotes or paraphrases what is actually on the page.
Addison should answer *from* the page, never fall back to "here's a link, go and
look" — that behaviour is what this tool exists to replace.

**Do:** ask it to read something that isn't words — a PDF, an image, a download
link.
**Expect:** a plain refusal in ordinary language ("that isn't a page I can read
as words", or similar). **No stack trace, no error code, no mojibake**, and no
wall of control characters presented as page content.

**Do:** ask it to read `http://localhost` (or your router's address, or
`http://169.254.169.254`).
**Expect:** a plain refusal. Addison must never reach inside the machine or the
home network on an address the model chose. If any of these returns page
content, **stop the pass and file it first** — it outranks everything else in
this document.

**Do:** ask for a very long page.
**Expect:** the answer arrives in reasonable time and, if the page was
shortened, Addison **says so** rather than quietly answering from a fragment.

## 4b. Activity panel — the site being reached

The owner's chosen mitigation for silent outward reach (2026-07-20) is
**visibility, not extra prompts**: once you have allowed one page read, every
later read in the session is ungated, so the panel naming the destination is the
only thing standing between the person and a read they never asked for. Treat a
missing host line as a safety failure, not a cosmetic one.

**Do:** run a page read (step 4a) and watch the activity panel.
**Expect:** under the **"Read a web page"** step, a second line naming the
**site** — the host only (`bbc.co.uk`), in **mono**, one step dimmer than the
label above it. Confirm it appears on **every** read, not only the first one
that showed a permission card.

**Do:** ask Addison to read a page whose address is long, or one that redirects.
**Expect:** the line shows the **host**, never the full address — no path, no
query string, no `?utm_...` tail. A long host **wraps onto a second line**; it
must not be truncated with an ellipsis, because the end of an address is the
part that says whose site it really is.

**Do:** repeat one read in **Developer** mode.
**Expect:** identical — the host line is not a Simple-only affordance.

**Do:** run a routine that reads a page.
**Expect:** the host still appears; the routine path is not a way around
visibility.

**Both themes.** Toggle light and dark and re-read the line in each. It must be
comfortably readable against the page background at its small size — dimmer than
the step label, but never so faint you have to lean in. A line nobody can read
is not visibility. Check it at the narrow-window width too (§14): the host wraps
inside the panel and never pushes the layout sideways.

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

## 13. Fern app shell (visual + flows, amended 2026-07 v3)

**First-run banner.** Launch with **no key configured** (fresh keychain or a
throwaway `ADDISON_DB_PATH` + removed provider keys). The chat column shows the
**pine setup banner** — "FIRST-TIME SETUP · STEP 1 OF 2", serif "Let's get
Addison ready.", step 1 as a filled cream circle, step 2 outlined — above a
serif time-of-day greeting ("Good morning/afternoon/evening.") instead of the
welcome message. **Start setup** opens Settings scrolled to the API keys card
with the first key input focused. Connect a key, come back: the banner reads
**step 2 of 2** ("Say hello"), step 1 shows ✓, and the composer is focused.
**Skip for now** hides it for this launch only (it returns on relaunch while
nothing is configured); once a key is configured at startup it never renders.

**Sidebar / history.** Conversations list in the left column under TODAY /
EARLIER, newest first; long titles ellipsize; the open conversation gets the
`hair` background + 2px fern left bar. **＋ New chat** resets the thread;
picking a past conversation loads it. Collapse (`«`) shrinks the sidebar to a
slim bell rail and persists across relaunch. **Settings** at the bottom gets
the same active treatment while the Settings screen is open.

**Settings page.** In-window (no drawer): two card columns on a wide window,
one column under ~900px; Escape or "Back to chat" returns. Cards: Where
Addison thinks (selected row = fern border + tint + ✓), API keys, Routines,
Run a model on this computer, Profile (+ Appearance).

**API keys (multi-provider).** Anthropic, OpenAI, Google, and "Your own
server" rows. A connected row shows "✓ Key saved · added <date>" with an
outlined **Replace** button and a **Remove** text button; Google collapses to
"Add key"; the custom row takes an `http(s)://…` base URL (mono) + optional
key. Saving shows "Checked with one tiny request…", a bad key shows a plain
error and still offers "Remove the saved key". Models from every connected
provider appear together in the composer's picker.

**Widgets / tray.** The right rail: YOUR WIDGETS + Edit, pinned widget cards
(routine rows with a fern-tint **Run** pill), the token meter ("TOKENS ·
<month>" small-caps + right-aligned mono count, 5px fern bar only when a limit
exists), and the connections card (fern dot = up, gray = idle, rose = down;
mono values right — no card title). Unpinned widgets sit behind the
stacked-edge "**N more widgets ▾**" tray row; Edit mode shows pin (⬤/◯) and
remove (✕) per stored widget. "＋ Ask Addison to build a widget" seeds the
composer. "Hide widgets »" moves Addison's-work + consent cards inline into
the thread and persists.

**Both themes.** Settings → Appearance flips light/dark instantly with a calm
cross-fade and **no white flash** on relaunch (the theme pre-paints before
CSS). Walk chat, sidebar, settings, and the rail in **both** themes: all text
legible, keyboard focus rings (Tab) clearly visible everywhere, the pine
banner keeps its fixed dark look in both.

**Fonts.** Message bodies, greetings, and settings headings render in the
**serif** (Source Serif 4 — real serifs, not a system fallback); UI controls
in Public Sans; machine facts only — token counts, latency, model ids, the
custom server URL — in **IBM Plex Mono**. No network font requests (bundled
woff2 only; check devtools Network offline if in doubt).

**Markdown + mermaid.** Ask for bold, lists, a table, and a fenced code block —
code highlights with the calm palette on the inset `surface` background, the
table gets hairline borders + small-caps headers. Ask for a small mermaid
flowchart — it renders as an SVG matching the theme; a **malformed** fence
falls back to plain code without breaking the row.

---

## 13a. Restore points (the G3 rollback floor)

The Settings card that makes global floor **G3** visible. Everything here is
recovery machinery, so a failure in this section outranks a failure anywhere
else in this document — file it first.

**Where.** Settings → **Restore points**, directly under Profile (deliberate:
the person who just changed their profile is one row away from undoing it).
The card is called "Restore points" everywhere in the UI, never "Snapshots".

**The list.** Rows are 8px `ProviderRow` shells: a plain-language label
("Working setup", "Before switching profile", "Before deleting a note",
"You saved this") in the semibold UI face, and its timestamp below in **mono**
(machine facts only). On a fresh profile there is exactly one row, **"Addison as
first installed"**, and it is marked **Permanent**.

**Automatic capture.** Do each of these and re-open the card — a new row appears
for each, and its label names the change: switch profile (Simple ⇄ Developer),
connect a provider key, remove a provider key, delete a routine, delete a widget,
delete a note (skill), edit a note. Then send one ordinary chat message and
re-open: a **"Working setup"** row appears. Send a second message without
changing anything — **no second row** (identical configs dedupe).

**Save a snapshot now.** The outlined button in the card's header (Diagnostics
"Clear" style, never fern-filled). One click adds a **"You saved this"** row
immediately.

**Restore, the two-step.** Click **Restore to the last working state** (fern
filled, rounded — a recovery is never styled as a destruction, so it must not
carry the rose `danger` token). Expect an inline fern-tint confirm block —
**never** a system `window.confirm` — reading *"Your settings, services, notes,
widgets and routines go back to how they were. Your chats and your saved keys
aren't touched."* The **target must be named above the buttons** with its
timestamp: Restore is never a click into the dark. **Not now** backs out and
leaves everything unchanged.

**The two extra sentences.** Make a change in Developer, switch to Simple, then
open the confirm: a second sentence must say Addison will switch back to
Developer — a restore can move you between profiles, and therefore between
safety modes, and the base sentence never said so. On a fresh install where the
only target is genesis, the second sentence must instead warn that services,
notes, widgets and routines are cleared.

**Restore actually restores.** Add a note, restore past it, and confirm the note
is gone, the widget rail matches, and **the chat history is untouched** — a
rollback restores configuration, it never erases chats. Re-open the API keys
card: a provider whose key is still in the keychain reconnects on its own; one
whose key was removed is **named in the result message**, not silently shown as
connected.

**Permanent rows refuse deletion.** The genesis row (and, from step 2, any
Custom-mode anchor) shows a blocky **PERMANENT** tag — square, 2px fern left
rule, small caps: it is something Addison is telling you about the record, not a
control — and has **no Remove control at all**. Ordinary rows do.

**Mode never hides a row.** Create a routine and a snapshot in Developer, switch
to Simple, open Restore points: **every row is still listed and still
restorable.** Routines and widgets made in Developer are hidden in Simple;
snapshots are the deliberate exception, because hiding them would hide the way
back from the person most likely to need it. An empty or shortened list here is
a **G3 failure**, not a cosmetic one.

**Both themes.** Walk the card in **light and dark**: row borders and the mono
timestamps read correctly, the **fern-tint confirm block** has enough contrast
against the card in dark, the **`text-fern-deep` PERMANENT tag** stays legible in
dark (it is the one place that token sits on a row background), the fern-filled
Restore button's label passes contrast, and Tab focus rings are visible on the
save button, the restore button, both confirm buttons, and every Remove.

**Narrow window.** Under 768px the card stacks into the single Settings column
and the save / restore / confirm buttons are all **≥44px** tall.

---

## 13b. Custom profile + guards (Phase-2 step 2 — the G4 anchor caller)

The Settings surfaces for the Custom profile and its two prompting guards. A
failure in the **anchor** steps below is a G4 floor failure — file it with the
same priority as §13a.

**Reaching Custom (deliberately deep).** On the Profile card, Custom must NOT
appear as an ordinary third option. An **"Advanced…"** disclosure reveals it;
selecting it shows a two-step inline confirm (never `window.confirm`) carrying
the honest description — *"Custom — for advanced users. Addison can do
everything the Developer profile allows, and you choose how often it asks you
first. Going back to a working setup always stays possible."* Backing out
changes nothing. Confirming switches the profile; a **"Before switching
profile"** restore point appears (§13a idiom).

**The guard panel.** Visible ONLY while Custom is active (switch to Simple or
Developer — the panel is gone, not greyed). Exactly TWO guards, nothing else —
if a third control ever appears here, that is a design breach, not a nit. Intro
line: *"These settings change how often Addison asks you before acting. They
never change Addison's ability to go back to a working setup."*

**Tightening is free.** Move either guard to a stricter option and save: no
confirm, no new permanent row.

**Weakening mints the anchor.** Move a guard to a weaker option and save: an
inline confirm first — *"Addison will ask you less often before acting. Before
this changes, Addison saves a permanent restore point of the last setup it saw
working — it can't be deleted, and you can always go back to it."* After
confirming, open Restore points: a **"Before turning a guard off"** row exists,
marked **Permanent**, with **no Remove control**.

**The anchor dedupes.** Weaken → tighten → weaken again (no chat messages in
between): still exactly ONE "Before turning a guard off" row. Send a chat
message between rounds (a new verified config) and weaken again: a second
anchor is now legitimate.

**Ask-once means everything the tool does.** With the destructive card set to
"Ask once" (Developer surface, so `!run`-style dev commands): approve one
destructive command — a DIFFERENT destructive command from the same tool then
runs with **no card**. That breadth is what the option's copy promises
("anything else it does goes ahead without asking"). Switch to Simple and back:
the next destructive action asks again — the approval died with the switch.

**Never-ask still logs.** With the scope on "Never ask", destructive actions run
without cards but every one still appears in the Activity Panel — fewer prompts,
not no gate.

**Per-row restore, permanent rows only.** In Restore points, Permanent rows
(anchor / genesis) carry **"Restore this one"** with the two-step confirm naming
the row; ordinary rows offer only Remove. Restoring the anchor works from
Simple too (§13a's C6 rule — the way back is never mode-hidden).

**The restore disclosure.** Under Custom: save a restore point while weakened,
tighten the guards, then restore that point. The result notice must include
*"Going back to this setup also turned down how often Addison asks before
acting."* — a recovery that lowers your protections says so.

---

## 14. Narrow window / mobile layout

Addison is a desktop app, so "mobile" = the **narrow-window layout** below the
**768px** breakpoint (the same one Tailwind's `md:` uses; it also future-proofs
a phone shell). Resize the window under 768px (or use a 375-wide device preset).
The desktop three-column layout at ≥768px must be **unchanged**.

**Top bar.** Below 768px the desktop chat header is replaced by a 3-element top
bar: **☰** (left, 44px), the **centered conversation title** (13px/600,
truncates), and the **bell** (right, 44px). No "Undo last action" or rail toggle
up here — those move into the widget sheet.

**Sidebar → slide-over drawer.** ☰ opens the sidebar as a **left slide-over**
(280px, `side` bg, scrim behind, 250ms slide). The collapse `«` control is gone
in drawer mode (the drawer is always full). It closes on: **scrim tap**,
**Escape**, and **picking** a conversation / Settings / New chat. Grow the window
back past 768px — the drawer is gone and the static 216px sidebar is back.

**Widget rail → bottom sheet.** The rail column is hidden below 768px; the
**bell** opens a **bottom sheet** (rounded top, `surface` bg, scrim, a 36×4px
drag handle, max-height ~70vh, its own scroll). It holds the SAME rail content —
YOUR WIDGETS + Edit, widget cards, token meter, connections, tray, the dashed
"Ask Addison to build a widget", and the "Addison's work" block — plus **Undo
last action** (right-aligned, above the widgets) when something is undoable.
Closes on: **scrim tap**, **Escape**, and **dragging the handle down**. Its open
state is **not persisted** (reopen = closed).

**Consent inline.** Below 768px a permission request **always renders inline in
the thread** (fern-tint card, Allow + Not now), never in the sheet — so a prompt
is never hidden behind the bell even with the sheet closed.

**Settings one column.** Below 768px the two columns stack into one flowing
column (unchanged from the ≤900px stack) and rows grow: selectable rows, provider
rows + inputs + Save/Add-key, and the Profile/Appearance segmented controls are
all **≥44px** tall.

**Hit targets ≥44px.** Spot-check with a ruler / devtools: drawer conversation
rows, New chat, Settings; composer **Send**/Stop; widget **Run** pills, the tray
"N more widgets" row, and the dashed add-widget button. (These use `max-md:`
utilities, so desktop keeps its compact sizes.)

**Safe area + no overflow.** In the DOM the top bar carries
`padding-top: env(safe-area-inset-top)` and the composer + sheet carry
`padding-bottom: env(safe-area-inset-bottom)` (both 0 on desktop, so harmless).
At **375px** there is **no horizontal scroll** anywhere, and the first-run pine
banner sits within ~16px side margins without overflowing.

**Reduced motion.** With the OS "reduce motion" setting on, the drawer and sheet
**appear instantly** (no slide) — same as the theme cross-fade.

**Both themes.** Walk the drawer, the sheet, the inline consent card, and
single-column Settings in **light and dark**: surfaces, borders, and text all
read correctly, focus rings visible.

---

## After the pass

For each failed step: screenshot + stderr → diagnose → fix → **add an
automated test that would have caught it** (pytest for core behavior, the
live-driver pattern in HANDOFF.md for end-to-end, cargo tests for shell
behavior). Then the UI/UX polish phase starts, fed by whatever this pass
surfaced.
