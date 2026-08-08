# Addison v1 — Manual Desktop Testing Pass

Structured pass over every subsystem, one numbered step each. Run the app with
`cd shell && npm run tauri dev` and keep that terminal visible — the engine's
stderr lands there, and it's the first thing to copy when something misbehaves.

How to report an issue: step number + screenshot + any stderr lines from the
launch terminal. Findings get converted into automated coverage afterwards.

Cheap-model tip: export `ADDISON_MODEL=claude-haiku-4-5` before launching if
you want the whole pass to cost pennies; steps 9–10 exercise the picker anyway.

**If a surface changed, retake the screenshots.** `docs/screenshots/` is generated
from the real frontend — `cd shell && npm run screenshots` with the dev server up.
The shell, empty state, composer, Settings, theme tokens and scramble timing are
all captured; a change to any of them dates the README's front page. See
[`screenshots/MANIFEST.md`](screenshots/MANIFEST.md).

**Which branch to run.** `master`. Sections 13, 13a, 13b and 14 describe the
**dark v4** UI, which merged with **PR #58** on 2026-07-26 — no branch switch is
needed any more. (This paragraph used to send you to `redesign/dark-v2`; that
branch is now fully contained in `master`, and the Fern look is gone from the
tree rather than merely superseded.)

**Known gaps — not bugs, don't file them** (see `KNOWN-GAPS.md`):
drafting a message reports "Opening email drafts isn't available yet."; there is
no file-attach/drop UI, so `read_file` is unreachable from chat; the Setup
Assistant relay has no server in this repo; nothing in the app can open an
external link, so every address shown in Settings is copy-paste text.

---

## 1. Launch & connect

**Do:** `cd shell && npm run tauri dev`. Wait for the window.
**Expect:** the window paints in the right theme immediately — no white flash,
because `index.html` sets the background before the bundle loads. The message box
is active with the placeholder "Write to Addison…"; the composer reads
"Addison's engine isn't connected yet." and a banner says "Addison's engine isn't
connected. You can look around, but I can't chat just yet." only while the core
is down. No error banner otherwise. Window looks calm and near-black: the dark
direction's `paper` background, hairline separators, one soft violet accent — and
the lowercase-`a` mark alone at the far left of the header.
**Fail signs:** either of those disconnected sentences still on screen after
~10 s, or a first message that never gets a reply → copy the launch-terminal
stderr.

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

## 13. App shell — the dark direction (visual + flows, amended 2026-07-26 v4)

The reference is `docs/design-brief-dark/` (README + `prototype.html`); the
prototype→app mapping is `IMPLEMENTATION.md` in that directory. Fidelity is
pixel-perfect for colors, type, spacing, copy and motion — but the prototype's
**content is demo data**, so anything on screen must come from real state.
**Fail sign that outranks the rest of this section: fabricated state.** A
connection, a chat, a model or a restore point that isn't really there is a bug
however good it looks.

**Header.** One ~56px row across the window, hairline `line` bottom border. Far
left is the **lowercase-`a` mark** alone (no "Addison" wordmark beside it) — it
is a real button: clicking it starts a new chat and returns to the thread. Then
`←` (surfaces only, back to chat) or the sidebar chevron `«`/`»` (chat only),
then the view title (13px, `ink-soft`) — which **resolves out of the scramble**
whenever it changes. Right: **"Undo last action"** in accent, shown *only* when
something is actually undoable, and the rail chevron (chat only, and only at
≥1024px where the rail has a column to appear in).

**Chat empty state.** A new chat shows a centred stack over a faint dotted
starfield — **no seeded "welcome" message**: the time-of-day greeting at 26px
("Still up?" before 05:00, then "Good morning." / "Good afternoon." / "Good
evening."), scrambling in; the subline "Ask anything, or hand me a chore.
Everything can be undone."; and three accent chips — "Tidy my Downloads
folder", "Draft an email", "Plan the weekend" — that **fill the composer**
rather than sending. The starfield never intercepts a click.

**First-run.** Launch with **no key configured** (fresh keychain or a throwaway
`ADDISON_DB_PATH` + removed provider keys). The empty state gains the 44px mark
above the greeting and, below the chips, the first-run block in the row idiom:
a header row "Let's get Addison ready." with mono "first-time setup · 1 of 2"
right, then two hairline step rows — "Connect a cloud account, or a model that
stays here." (mono **now**, in accent) and "Say hello — Addison introduces
itself." (mono **next**) — then **Start setup** (accent) and **Skip for now**.
There is no filled block, no cream, no serif anywhere. **Start setup** opens
Settings at the API-keys section with the first key input focused. Connect a
key, come back: the header flips to "You're set up. Say hello to Addison.",
mono reads **2 of 2**, step 1 shows **done ✓**, and the composer is focused.
**Skip for now** hides it for this launch only (it returns on relaunch while
nothing is configured); once a key is configured at startup it never renders.
Check this at **1280×620** specifically: both actions must sit inside the fold
(the block is on a measured height budget — see the file header of
`FirstRunBanner.tsx`).

**Sidebar / history.** The 212px left column, top to bottom: a **Workspace**
block (11px label on a 2px `rail` rule) with **Tools** and **Snapshots** rows,
each carrying a mono hint (trusted-folder count or policy mode; restore-point
count); **＋ New chat** in accent; then real conversations grouped **Today /
Earlier**, newest first. A group header is an 11px `faint` label with a mono
hint (`N chats` / `collapse`) and toggles on click: collapsed shows 3 rows plus
a ghost "**N more…**" row; expanding animates *only* the newly revealed rows;
collapsing plays fadeDrop on the rows beyond 3 and commits (~290ms), and
re-clicking mid-collapse cancels cleanly. Chat rows are 12px — title ellipsized
plus a mono time; the active row is a **2px accent left rail** with `ink` text,
never a filled background. **Double-click a row to rename** — that survives.
Switching chats scrambles the row labels and bodies back in (staggered). The
footer is pinned to the bottom: a **Settings** row (same accent-rail treatment
while that surface is open) over a mono `{Simple|Developer|Custom} profile ·
local` note (plus ` · open` when the mode is open). Collapse (`«`) animates the
column to zero width and persists across relaunch — and a collapsed column must
be **out of the tab order** (Tab must not land on Tools/Settings with the focus
ring rendering nowhere).

**Composer.** Borderless textarea (15px) over a 1px top border that goes
`track` → `track-hi` on focus. The text wraps **full width** above a controls
strip; the placeholder is "Write to Addison…" ("Addison is working…" mid-turn,
"Addison's engine isn't connected yet." when the core is down). Right of the
strip: the model label in mono 10.5px, then a 30px **circular** send button —
transparent with a `track` border when idle, accent fill with an `on-accent` ↑
when there's text, and **while working it becomes the Stop control** (border
circle, ■, title "Stop"). Below, mono 10px `ghost`: "enter to send · everything
can be undone". Type enough to overflow and the composer grows to a
**line-grid** maximum and scrolls with its own bespoke scrollbar in a reserved
lane — the bar must never sit on top of the text.

**Composer model menu.** Click the mono model label: a menu opens *above* it,
bottom-right anchored, ≥196px, `panel` background, `rail` border, 6px radius,
with a 10px "Answer with" header. Rows are mono name + mono note, the selected
one carrying ✓ and an accent note; hovering fills the row with `line`. Rows come
from the **real** catalog (roles + cloud + local models) and the note is derived
from real flags — `free` stays Ollama-only, and no cloud model may ever claim
it. When the picked cloud model has effort levels, an "Effort" section follows
in the same idiom. Footer hint: "picked per message · default in Settings".

**Surfaces.** Settings, Tools, Snapshots and Build a widget replace the chat
column (they are not a drawer and not a modal): centred ≤580px, scrollable
behind the vertical fade mask, 20px title + 13px `muted` description, sections
as an 11px label on a 2px `rail` rule with rows separated by 1px `line` top
borders. Row anatomy is **name — spacer — mono value — accent action**; there
are no bordered cards and no pills. Entering staggers the children in (fadeRise
40ms apart); leaving plays fadeDrop and commits at ~240ms. **Escape** returns to
chat from any of them.

**Settings sections, in order:** Where Addison thinks · Which model answers ·
API keys · Run a model on this computer · Routines · Skills · Profile · How
careful Addison is (Custom only) · Folders Addison may work in
(Developer/Custom) · **Tool servers** (Developer/Custom) · Restore points ·
Diagnostics. The description reads
"Everything lives on this computer. Nothing leaves it without asking you
first." Selecting a model role opens the **model popup** — a fixed-position
270px `panel` with a 7px radius and a hairline ring, positioned so the selected
row lands near the click and clamped ≥12px from the viewport edge; the selected
row is a 2px accent rail. Click outside to close.

**The model lists are a folder tree, in BOTH panels.** The Settings popup and the
composer's menu draw the same rows from the same engine, so anything true of one
must be true of the other. Company, then family, then model, indented a step each:
opening Google closes Anthropic, and opening a family closes the one open beside it —
**one folder open at a time is the whole idiom**, and two open at once is a bug. Each
panel opens with the model that is actually in effect already revealed, so changing
your mind is never a hunt. A model the provider has refused is struck through, dimmed,
sunk to the end of its family, carries the provider's own sentence underneath — and is
still pickable, because a refusal may have been a bad afternoon.

**And the tree has a keyboard, the same one in both panels.** Open either with the
keyboard: focus lands inside the list, and **Tab does not walk the rows** — the whole
tree is one stop. Up and Down move through what is drawn and wrap at the ends; Home
and End jump; Right opens a closed folder and steps into an open one and does nothing
on a model; Left closes an open folder or climbs to its parent; Enter or Space acts.
Escape closes, and **focus returns to the control you opened it from** — the composer's
model label, or the Settings row's "change". In the composer's menu, where an Effort
section follows the list, Tab cycles tree → effort → tree rather than leaving: a
keyboard user must be able to choose the effort the label is advertising back at them.
A screen reader should announce each row's level and its position ("3 of 7"), and a
folder as expanded or collapsed.

**API keys (multi-provider).** Anthropic, OpenAI, Google, and **"Your own
server"** rows. A disconnected row offers "add key" ("connect" for the custom
one); a connected row offers **Replace** and **Remove the saved key**. The
custom row takes an `http(s)://…` base URL in mono plus an optional key. Saving
shows "Checked with one tiny request, then locked away in the keychain."; a bad
key shows a plain-language error and still offers to remove the saved key.
Models from every connected provider appear together in the composer's picker.

**Tools surface.** "What Addison can reach on this computer. Connect only what
you're comfortable with." **Real data only** — Connected lists providers that
actually have keys, trusted folders (each with a revoke action), and the local
model once it's ready; Available lists providers without keys, whose action
jumps to the API-keys section. There must be **no fake IDE / Email / Calendar
rows** (the prototype has them; they are demo content).

**Widgets rail.** The 232px right column on chat, hairline-separated rows: the
**"Addison's work"** step list while a task runs (5px dots, the current one
blinking, finished steps dimmed to `muted`, from real ActivityUpdates), the
accent **"Save as routine"** link, the token meter (**"Tokens this month"** with
a 2px track and `ink` fill, from real `stats.get`), routine widgets as
`name — Run` rows, stat widgets as name/value rows, the three interactive kinds
(a **checklist** you tick, a **note** you edit, a **timer** you start and pause —
step 6 half A, 2026-08-06), and the footer **"＋ Ask
Addison to build a widget"**, which opens the Build-a-widget surface. Unpinned
widgets sit behind an "**N more widget(s)**" tray row; **edit** (footer, flips
to **done**) reveals the pin toggle (⬤/◯) and remove (✕) per stored widget.
Hiding the rail («) moves the work + consent blocks inline into the thread, and
the choice persists.

**Motion (the signature).** The character-scramble is the app's one flourish:
it plays on initial load (staggered), on any leaf text element you click, on
view-title change, on switching chats, and on the greeting. A **streaming
reply** gets the same language applied to real text — a ~14-char scrambled
window trailing the incoming tail, resolving left→right, with a 7×14px blinking
block cursor riding along while Addison works. Two things to check hard: the
final text must be **byte-exact** once a reveal finishes (scramble must never
corrupt an answer), and you must be able to **scroll up mid-reveal to reread**
without the thread yanking you back down. Then turn the OS "reduce motion"
setting on and repeat: every animation is a no-op, and text is never rewritten.

**Both themes.** Settings → Appearance cycles **Light / Dark / Match this
computer** (default: Match this computer) and flips instantly, with **no white
flash** on relaunch (the theme pre-paints in `index.html` before the bundle
loads). Walk chat, sidebar, surfaces and the rail in **both** themes: all text
legible, keyboard focus rings (Tab) clearly visible everywhere. Dark is the
designed reference; light is a derived translation, so structure must be
identical and only values differ.

**Fonts.** System stacks only — `'Helvetica Neue'` for UI, `ui-monospace`/`SF
Mono` for machine facts (token counts, latency, model ids, timestamps, the
custom server URL). There is **no serif anywhere** and **no bundled font**: in
devtools the Network tab must show no font requests at all, and the app must
carry no `@font-face`.

**Markdown + mermaid.** Ask for bold, lists, a table, and a fenced code block —
code highlights with the calm palette on a `panel` block with `line` hairlines,
the table gets hairline borders. Ask for a small mermaid flowchart — it renders
as an SVG matching the theme; a **malformed** fence falls back to plain code
without breaking the row.

---

## 13a. Restore points (the G3 rollback floor)

The Settings section that makes global floor **G3** visible. Everything here is
recovery machinery, so a failure in this section outranks a failure anywhere
else in this document — file it first.

**Where.** Settings → **Restore points**, directly under Profile (deliberate:
the person who just changed their profile is one row away from undoing it).
It is called "Restore points" everywhere in the UI, never "Snapshots" — except
as the name of the sidebar's **Snapshots** surface, which shows the same rows.

**The summary row.** The section leads with the one-action way back: a row
reading **"Going back to {target}"** — the target named in bright `ink` — with
its timestamp as the mono value and **restore** as the accent action. Below it,
**"All restore points"** with a mono count and an **open** action, which raises
the modal.

**The list** (modal, or the Snapshots surface). One row per restore point: a
plain-language label ("Working setup", "Before switching profile", "Before
deleting a note", "You saved this") with its timestamp as the **mono** value
(machine facts only). On a fresh profile there is exactly one row, **"Addison as
first installed"**, marked **Permanent**. The modal is a centred 440px `panel`
over a scrim, header "Restore points" beside **save one now** and a ✕, and a
mono footer note whose first half is **mode-scoped**: "everything can be undone ·
restores never delete your files" under Simple (SAFE), but "some actions can't be
undone · restores never delete your files" under Developer/Custom (OPEN), because
`run_command` is SAFE-2's one exemption and the footer must not contradict the
profile card. Check it in both. It closes on the scrim, the ✕, and Escape, and
focus returns to the opener.

**Automatic capture.** Do each of these and re-open the list — a new row appears
for each, and its label names the change: switch profile (Simple ⇄ Developer),
connect a provider key, remove a provider key, delete a routine, delete a widget,
delete a note (skill), edit a note. Then send one ordinary chat message and
re-open: a **"Working setup"** row appears. Send a second message without
changing anything — **no second row** (identical configs dedupe).

**Save a restore point now.** **save one now**, in the modal's header — a mono
accent action, never the danger token. One click adds a **"You saved this"** row
immediately, and a successful save is what clears a sticky capture-failure
warning.

**Restore, the two-step.** Click **restore** on the summary row (accent — a
recovery is never styled as a destruction, so it must never carry the rose
`danger` token). Expect an **inline** confirm block, indented on a 2px `rail`
rule — **never** a system `window.confirm` — reading *"Your settings, services,
notes, widgets and routines go back to how they were. Your chats and your saved
keys aren't touched."* The **"Going back to {target}" line must stay on screen
above the confirm**, with its timestamp: Restore is never a click into the dark,
and the row's name must not be replaced by the consequence copy at the moment
the person is deciding. While the confirm is open there is exactly **one** live
restore control. **Not now** backs out and leaves everything unchanged.

**The two extra sentences.** Make a change in Developer, switch to Simple, then
open the confirm: a second sentence must say Addison will switch back to
Developer — a restore can move you between profiles, and therefore between
safety modes, and the base sentence never said so. On a fresh install where the
only target is genesis, the second sentence must instead warn that services,
notes, widgets and routines are cleared.

**Restore actually restores.** Add a note, restore past it, and confirm the note
is gone, the widget rail matches, and **the chat history is untouched** — a
rollback restores configuration, it never erases chats. Re-open the API keys
section: a provider whose key is still in the keychain reconnects on its own;
one whose key was removed is **named in the result message**, not silently shown
as connected. A restore that really landed shows **restored ✓** in mono accent
on the row — and only then.

**Permanent rows refuse deletion.** The genesis row (and any Custom-mode G4
anchor) shows a blocky **Permanent** tag — small caps on a 2px accent left rule:
it is something Addison is telling you about the record, not a control — and has
**no Remove control at all**. What it gets instead is its own **Restore this
one**, in accent, with the same two-step inline confirm that names the row and
its timestamp before the click. Ordinary rows carry only **Remove** (the one
place the danger token is correct here).

**Mode never hides a row.** Create a routine and a snapshot in Developer, switch
to Simple, open Restore points: **every row is still listed and still
restorable.** Nothing in the app hides a row by mode any more — routines and
widgets made in Developer are *listed and disabled* in Simple (see below), and
snapshots were always the deliberate exception, because hiding them would hide
the way back from the person most likely to need it. An empty or shortened list
here is a **G3 failure**, not a cosmetic one.

**A Developer-made routine or widget WAITS in Simple; it never disappears.**
Make a routine (and a widget) in Developer, switch to Simple, open Settings →
**Routines** and look at the rail. **Expect:** both are still there, annotated
**Waiting** / `waiting`, with the sentence *"That routine uses developer
abilities, so it's waiting in Developer profile."* (widgets say "That widget…"),
**no Run control**, and — for a command widget — **no command text on screen**.
Remove still works. Switch back to Developer: both are usable again, untouched.
A row that vanishes instead is the bug this replaced (owner decision 2026-08-06);
a row that offers Run is worse, because the core will refuse it.

**The honest silences.** When restore points exist but the one-action restore
has no target, the section must **say which silence this is** rather than going
quiet: either *"Your setup already matches your last working setup, so there's
nothing to go back to right now."* or *"None of these has been seen working yet,
so the restore button isn't ready. It appears after Addison next answers you."*
A list of restore points with no action and no explanation is a G3 failure — the
reader who most needs the floor reads that silence as the floor being broken.

**Both themes.** Walk the section and the modal in **light and dark**: the
hairline row borders and the mono timestamps read correctly, the confirm block's
`rail` rule is visible against `paper`, the accent **Permanent** tag stays
legible, the accent **restore** action passes contrast, and Tab focus rings are
visible on save one now, restore, both confirm actions, every Remove, and the
modal's ✕. Tab and Shift+Tab must **wrap inside the modal** while it is open.

**Narrow window.** Under 768px the section stays in the single Settings column
and the save / restore / confirm actions are all **≥44px** tall.

---

## 13b. Custom profile + guards (Phase-2 step 2 — the G4 anchor caller)

The Settings surfaces for the Custom profile and its two prompting guards. A
failure in the **anchor** steps below is a G4 floor failure — file it with the
same priority as §13a.

**Reaching Custom (deliberately deep).** In the Profile section, Custom must NOT
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

## 13c. The Developer review surface (Phase 3 — the code screen)

**Written before the build, and the surface does not exist yet — skip this section
until it ships.** The repo's rule is authoritative docs before code, so this is here
first on purpose; [`phase-3-review-surface-plan.md`](phase-3-review-surface-plan.md)
owns the build and [`../ROADMAP.md`](../ROADMAP.md) owns whether it has happened. The
copy quoted below is the plan's, and quoting it **is** the assertion: if the app says
something else, either the app is wrong or the plan changed and nobody amended this.

**This claims §13c, and names the debt it does not pay.** §13c was owed to **Phase-2
step 5** — the coding harness and workspace trust shipped 2026-07-24 with no manual
section at all — and that debt is **still unpaid**. The plan asked for the next free
letter at build time rather than a hardcoded one, and the next free letter was this
one; the harness's own section now takes **§13d**.

**Reaching it.** In Simple there is no code screen and no route to one — no sidebar
entry, no keyboard path. Switch to Developer (or Custom) and the entry appears. The
gate is on the **profile**, not the mode, and it is not decoration: trusted folders
survive a profile switch, so a Simple window that can still browse a folder trusted
under Developer is a floor-shaped failure, not a nit.

**The tree tells the truth about the disk.** With a folder trusted, `.git` and
`node_modules` are **listed** — collapsed, never auto-expanded, never hidden. A tree
that hides them is lying about what is on disk, and telling the truth is this
surface's whole value. A symlink is drawn as a symlink and not as a folder; one that
points out of the trusted root is dimmed and says *"this points outside the folder
you trusted"*. Click it anyway: it refuses. The label is honesty; the refusal is the
boundary, and it is the refusal that has to hold.

**A diff of a change that is still there.** In Developer, have Addison edit two
files, then open the surface: both are listed with a real before/after, the left pane
showing the file as it was before Addison's **first** change. Ask Addison to edit one
of them a second time — it stays **one** entry, still comparing against that original
version, not against the intermediate one. Revert it: the file on disk becomes exactly
what the left pane was showing, and the entry leaves the list. Nothing partial is
offered — there is no hunk-level revert, because a half-applied revert would put a
combination of bytes on disk that never existed there.

**The warning before a clobber.** Change one of those files yourself after Addison
did, then revert it. The confirm must say so — *"You've changed this file since
Addison did. Reverting will replace what's there now with the version from before
Addison's first change."* — as a two-step inline confirm, never a browser dialog.

**The restart is the step people will skip, and the one that matters.** Quit, reopen,
and look at an edit from the previous session. There must be **no Revert button that
fails**: the row is read-only, with *"Addison changed this before the app was last
restarted, so it can't put it back for you. The earlier version is on the left; you
can copy it."* The before text is right there, so the honest answer is still a useful
one. "Undo last action" in the header must be equally honest about the same edits —
a control that is offered and then refuses is worse than one that explains itself.

**Consent follows you here.** With the code screen open, ask Addison to do something
that needs approval. The permission card and the Activity Panel appear **on this
screen**, not back in chat — a consent surface you have navigated away from is
unanswerable, and two consent surfaces is the bug.

**Nothing new is offered to the model.** Open Tools in Developer: there is no
"browse files" or "list directory" tool, and there must never be one — a person
clicking a folder open is not the model acting. The other half of the same rule:
expanding a folder must **not** raise a permission card. A card in front of a click
somebody just made teaches people to dismiss cards.

**Diagrams, re-checked — this screen changes them.** It ships with a widened
`style-src`, and that policy is global: a Simple user gets it too, for a screen they
can never reach. The visible consequence is somewhere else entirely — mermaid's
injected SVG carries a `<style>` block that today's policy silently drops. **Re-run
§13's mermaid pass in both themes** and confirm diagrams still render as they did.
Then look at a permission card again, on this screen and in chat, and confirm nothing
overlays, hides or restyles it.

**Narrow window.** Take it through both breakpoints from §14. The tree collapses to a
rail and then to a drawer rather than squeezing the diff into two unreadable columns,
and the diff falls back to a single-column (inline) view instead of side-by-side.
Everything tappable — tree rows, file rows, Revert, both steps of its confirm — is
**≥44px** tall. At 375px there is no horizontal scroll anywhere.

**Both themes.** Walk the tree, the viewer and a diff in **light and dark**:
additions and deletions read as additions and deletions in both; the deletion tint is
the same rose the app already uses for destructive actions, and no fourth colour has
appeared; code tokens match what a fenced code block inside a message already looks
like. Then flip Appearance **while the viewer is open** — it re-themes immediately. A
viewer still showing the old theme until you navigate away is exactly what this step
exists to catch.

---

## 14. Narrow window / mobile layout

Addison is a desktop app, so "mobile" = the **narrow-window layout**. There are
**two** breakpoints to walk, not one, and they do different things:

- **1024px** — below it the right rail has no column, so the widgets move
  **inline to the foot of the thread**.
- **768px** (Tailwind's `md:`) — below it the sidebar becomes a **drawer**.

Resize the window through both (or use a 375-wide device preset). The desktop
three-column layout at ≥1024px must be **unchanged**.

**Header stays.** The header is *not* replaced on a narrow window — the mark,
the view title, and "Undo last action" stay exactly where they are. Two things
change: the sidebar chevron `«`/`»` becomes **☰** (44px), and the rail chevron
**disappears** below 1024px, because there is no rail column for it to toggle.
The ☰ is present on surfaces too, not just chat: on a phone the `←` alone would
strand you with no way to reach Tools, Snapshots or your chats without going
back to the thread first.

**Widget rail → inline.** Between 768px and 1024px, and below it, the rail
column is gone and the SAME rail content renders inline at the foot of the
thread on the chat view: the "Addison's work" block, the consent card, the
token meter, widget rows, the tray row, and "＋ Ask Addison to build a widget".
Nothing is dropped and nothing is hidden behind a control. (On a wide window the
work + consent blocks fall inline the same way whenever the rail is hidden.)

**Sidebar → slide-over drawer.** Below 768px, ☰ opens the sidebar as a **left
slide-over** (280px, `paper` background, scrim behind). It is a real **modal**:
`role="dialog"` + `aria-modal`, focus moves into the panel on open and back to
the ☰ on close, and **Tab cycles inside it** — tabbing through to the page
behind the scrim is a bug. It closes on the **scrim tap**, its own close arrow,
**Escape**, and **picking** a conversation / Settings / Tools / Snapshots / New
chat, and it plays the slide-out on every one of those paths. Grow the window
back past 768px — the drawer is gone and the static 212px sidebar is back.

**Consent inline.** A permission request renders **inline in the thread**, never
inside the drawer — a prompt must never be hidden behind a menu the person has
closed.

**Settings one column.** The surfaces are a single centred column at every
width, so nothing re-flows below 768px; what changes is that rows grow —
selectable rows, provider rows + inputs, the profile/appearance actions, the
first-run actions, and the empty-state suggestion chips are all **≥44px** tall
(`max-md:` utilities, so desktop keeps its compact sizes).

**Hit targets ≥44px.** Spot-check with a ruler / devtools: ☰, drawer
conversation rows, New chat, Tools, Snapshots, Settings; composer **Send**/Stop;
widget **Run** actions, the tray "N more widgets" row, and the add-widget row.

**Safe area + no overflow.** In the DOM the drawer's sidebar carries
`padding-top: env(safe-area-inset-top)` and the composer carries
`padding-bottom: env(safe-area-inset-bottom)` (both 0 on desktop, so harmless).
At **375px** there is **no horizontal scroll** anywhere, and the first-run block
sits within the column's side margins without overflowing.

**Reduced motion.** With the OS "reduce motion" setting on, the drawer opens and
closes **instantly** (no slide, and it must still unmount — the exit is normally
driven by `animationend`, which never fires when there is no animation). The
scramble is a no-op everywhere too, and no text is rewritten.

**Both themes.** Walk the drawer, the inline widgets, the inline consent card,
and the surfaces in **light and dark**: backgrounds, hairlines, and text all
read correctly, focus rings visible.

## 15. Interactive widgets (Phase-2 step 6 half A — checklist, note, timer)

Three SAFE widget kinds shipped 2026-08-06 and **no human has used them.** They
invoke no tools, so nothing here is a safety step; what it checks is that what
you *did* with a widget is kept apart from what the widget *is*.

**Make one of each, in Simple.** Ask Addison for a checklist, a note and a timer.
Each arrives as a confirm card, then a row in the side panel. Plain language, no
jargon; the accent is on actions only, never decoration.

**Tick, type, start.** Tick two checklist lines — done lines read as done by more
than colour (struck through, and the `n/total` count moves). Edit the note and
click away; the text commits on blur. Start the timer, then pause it: the clock
runs while you watch and **nothing rings and nothing fires at zero** — Addison
never triggers itself (G2), and a widget that fired would be exactly that.

**Keyboard.** Reach a checkbox by Tab and toggle it with Space. Personas are 54
and 68; a checkbox that needs a mouse is a failure, not a nit.

**THE STEP THAT MATTERS — state survives a relaunch.** Quit the app fully and
reopen. Ticks, note text and timer position are all still there.

**And a restore must NOT undo your ticks.** Take a restore point, tick another
line, then restore. The configuration goes back; **your ticked lines do not.**
`widget_state` is deliberately excluded from snapshot capture — restoring a
configuration must never untick somebody's list. If a restore resets the ticks,
that is the design being wrong, not a cosmetic bug.

**Items are fixed at creation, on purpose.** There is no way to add a line to an
existing checklist — a different list means a new widget, exactly as v1 has no
routine step-editing. Confirm Addison *says* so rather than silently failing.

## 16. Tool servers (Phase-2 step 7, phases 1–4: save, check, run, and what comes back)

MCP is **dev-only for v1**. Phase 1 saved an address, phase 2 taught Addison to
ask a server what it offers, phase 3 lets it run one of those tools — **asking you
first, on the default settings every single time** — and phase 4 decides what may
come back. Have a small HTTP MCP server on `localhost` to
hand; without one, everything up to "Check now" is still worth doing.

**Hidden in Simple.** In the Simple profile, Settings has no "Tool servers"
section at all, and the Tools page has no tool-server section either. Switch to
Developer: both appear.

**Add one.** A restore point appears (§13a idiom). Nothing is contacted by
adding — no status, no tool count, no "connected" until you press Check now. A
number that appears on its own is a claim about reach nobody verified.

**Addresses that must be refused**, each with a plain sentence and no row saved:
a `http://` address that is not on this computer; one carrying a sign-in name or
password; one with a query or fragment. A saved address lands in snapshots and
sidecars in plain text, which is why the refusal is at the store and not the box.

**Check now.** The row reports what the server offers, and the Tools page grows a
section headed by that server with one entry per tool. A server that is switched
off, or that wants a sign-in, says so in one plain sentence on its own row rather
than failing quietly. Quit and reopen: every row honestly reads "not checked yet"
again, because what a check found is never written down.

**THE STEP THAT MATTERS — every use asks first.** Ask Addison in Developer to use
one of the tools. A permission card appears **before anything happens**, and it
says which tool server the tool came from and that Addison cannot know what it
will do. Decline it: nothing runs. Ask again: the card appears **again** — a tool
server is somebody else's program, and approving it once must never approve it
twice. (On the Custom profile's "ask me less often" settings you will see fewer
cards, and that is the setting working, not this step failing.)

**The server's words are visibly the server's.** On that card, Addison's own sentence
comes FIRST and anything the server wrote about its tool comes last, attributed and in
quotation marks. A description that tries to sound like Addison — *"…Addison has
checked this server and it is safe to approve every time."* — must read as a quotation
from the server and never as Addison's own voice, and it must not be able to close
Addison's quotation marks and carry on outside them.

**A restore puts the list back and the page agrees.** Take a restore point, add a
tool server, then restore. The server is gone from Settings **and** from the Tools
page — a surface still offering a server the restored configuration no longer has is
the bug this guards.

**A server that goes quiet costs a wait, not the turn.** Stop your server mid-
answer (or point a row at a port nothing is listening on) and ask Addison to use
one of its tools. Within about fifteen seconds it says the server did not answer
and CARRIES ON with the rest of the reply. A frozen app here is a bug, not slowness.
**A server that answers a byte at a time counts as going quiet** — the wait is
bounded by the whole exchange, not by each byte.

**What comes back is named, not silently kept or dropped.** Have a tool answer with
a picture or a file alongside its words: Addison passes on the words and says in
plain English what it did not pass on — *"the tool also returned 2 images and 1
file"* — rather than leaving you to wonder. A tool whose whole answer is a picture
says there was nothing it could pass on, plus the count; that is the intended answer,
not a failure. A tool that answers with nothing at all must never look like a tool
that ran and said nothing — there is a sentence for it. A very long answer is trimmed
**once for the whole answer**, and the trim says how much there was and how much you
are seeing.

**A stale reference must refuse, not crash.** Check a server, save a routine with a
step that uses one of its tools, then quit and reopen (nothing is checked on start, so
that tool is not registered any more) and run the routine. The step must FAIL with a
plain sentence and the run must finish — a routine stuck reporting itself as still
running is the bug this guards.

**Remove is not trapped by a profile switch.** Go back to Simple: saved servers
stay listed and Remove still works — hiding somebody's saved configuration, or
making it un-removable, is the failure the 2026-08-06 artifact decision reversed.
Only *adding*, *checking* and *running* are refused there. Ask Addison in Simple
to use a tool server's tool: it will not, and it says so plainly.

**And a removed server is not called.** Check a server, then remove it, then ask
Addison to use one of its tools in the same session. It refuses — the address is
looked up when a tool is used, not remembered from the check.

---

## After the pass

For each failed step: screenshot + stderr → diagnose → fix → **add an
automated test that would have caught it** (pytest for core behavior, the
live-driver pattern in HANDOFF.md for end-to-end, cargo tests for shell
behavior). Then the UI/UX polish phase starts, fed by whatever this pass
surfaced.
