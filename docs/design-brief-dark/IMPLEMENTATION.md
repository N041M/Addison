# Addison dark redesign — implementation brief (2026-07-25)

This directory is the **authoritative design brief** for the app's look as of
2026-07-25. It supersedes `docs/design-brief-fern` (which stays in the tree as
history). The reference is `README.md` (the designer's handoff) plus
`prototype.html` + `support.js` (open in a browser; all styling is inline in the
markup, all behavior in the `<script data-dc-script>` class at the bottom).
**Fidelity is pixel-perfect** for colors, type, spacing, copy, and motion.

This file records how the prototype maps onto the real app. Two standing rules
resolve every conflict:

1. **The prototype's *content* is demo data; its *design* is binding.** Real
   conversations, real models, real snapshots, real settings replace the sample
   chats ("Jana", the landlord, fake IDE/Calendar connections). Never ship a
   control that displays fabricated state.
2. **The app's real features always survive.** Where the app has functionality
   the prototype doesn't show (Stop while working, Custom profile + guard panel,
   workspace trust, offers/proposal cards, markdown rendering, the restore
   confirm flow, mobile layout, three-way appearance), it is restyled into this
   language — never deleted, never de-wired. Safety copy and owner-decision
   flows (HANDOFF.md) keep their semantics byte-for-byte; only their skin
   changes.

## Tokens (Tailwind names → values)

Dark is the designed reference (exact hex from the handoff). Light is a
**derived translation** (not in the handoff — keep structure identical, only
swap values). CSS variables live in `styles.css` (`:root` = light, `.dark` =
dark), `darkMode: "class"` stays.

| token        | dark (reference) | light (derived) | use |
|--------------|------------------|-----------------|-----|
| `paper`      | `#0C0C0D`        | `#F7F7F5`       | app background |
| `panel`      | `#141518`        | `#FFFFFF`       | popovers, menus, modal |
| `line`       | `#1E1F22`        | `#E4E4E1`       | 1px hairlines/separators |
| `rail`       | `#2E2F33`        | `#D6D6D2`       | inactive 2px section rails; menu/modal borders |
| `track`      | `#26272B`        | `#8C8E94`       | composer idle top border, meter track, idle send ring |
| `track-hi`   | `#3A3B40`        | `#6E7076`       | composer/input focused border |
| `ink`        | `#E9E9E7`        | `#1B1B1D`       | primary text |
| `ink-soft`   | `#B9BBBE`        | `#43454A`       | secondary text |
| `muted`      | `#909398`        | `#5B5D63`       | tertiary text |
| `faint`      | `#6E7076`        | `#63656B`       | section labels |
| `disabled`   | `#55575C`        | `#696B71`       | hints, idle glyphs, "You" label |
| `ghost`      | `#3C3E42`        | `#6E7076`       | faintest microcopy |
| `accent`     | `#B4A9F5`        | `#6D5BD0`       | links/actions, selection rails, send fill |
| `on-accent`  | `#100E22`        | `#FFFFFF`       | glyph on accent fill |
| `scrollbar` / `scrollbar-hi` | `#2E2F33` / `#3A3B40` | `#888A90` / `#6E7076` | the composer's bespoke scrollbar thumb, idle / hover |

> **The light column above was corrected on 2026-07-26 and the values it replaced
> shipped nowhere.** Translating the dark ramp by eye produced text nobody could
> read on paper — measured, `ghost` was 1.53:1, `disabled` 2.17, `faint` 2.94, all
> against a 4.5 floor, at 10–11px, for readers who are 54 and 68; and `track`, which
> draws the composer's only boundary *and* the idle send-button ring, was 1.14:1, so
> that ring was effectively invisible. Dark keeps its designed values untouched.
> Light's low end is now pinned to the floor and therefore **compressed**
> (muted 6.14 → faint 5.43 → disabled 4.97 → ghost 4.61); the ramp still descends
> and every rung is legible. `muted` moved with them, because leaving it at 4.61
> would have made `ghost` its equal and inverted the ramp it heads. `track` /
> `track-hi` are treated as UI components (3:1 floor), not decorative hairlines.
> `styles.css` carries these ratios inline — it, not this table, is the source of
> truth if they ever diverge again.

Scrim: `rgba(0,0,0,.55)` both themes. Popover ring (dark):
`0 0 0 .5px rgba(255,255,255,.07), 0 18px 48px rgba(0,0,0,.65)` — light uses
`0 0 0 .5px rgba(0,0,0,.08), 0 18px 48px rgba(0,0,0,.18)`. Modal shadow:
`0 24px 64px rgba(0,0,0,.6)` (light `.2`). The composer's model menu gets its own
shallower lift, `--shadow-menu` (`0 12px 32px rgba(0,0,0,.14)` light), rather than
reusing the anchored popover's. Danger keeps a rose token for real
destructive actions (delete widget/routine) — value `#E2A6A6` dark / `#B4544E`
light; restores are NEVER danger-colored (they are recoveries, HANDOFF rule).

**Fonts.** UI: `'Helvetica Neue', Helvetica, Arial, sans-serif`. Mono:
`ui-monospace, 'SF Mono', Menlo, monospace`. System stacks only — the bundled
OFL woff2 files and every `@font-face` are removed (serif is gone from the app).

**Type scale** (font-size tokens; line-height only where stated):
surface/page title 20px (`-.01em`); greeting 26px (`-.01em`); header title
13px/500; body message **15.5px / 1.65**; composer text 15px/1.5; empty-state
subline 14px; rows & most labels 12px; section labels **11px/500/.04em**;
mono meta 10–10.5px; menu header 10px. Buttons/links inherit row sizes.

**Radii:** popover 7px, modal 8px, composer-menu 6px, menu rows 4–5px, send
button 50%. **No pill radii, no 10–12px cards** — surfaces are flat hairline
rows now, not floating cards.

**Shape rule:** selection/active is a **2px accent left rail** (chat rows,
model rows, settings nav); section labels sit on a 2px `rail` left rule.
Hairline `line` top-borders separate rows. Cards-with-borders are reserved for
floating chrome (popovers, modal, composer menu).

**Keyframes** (in styles.css): `fadeRise` (opacity 0→1, translateY 6px→0),
`fadeDrop` (exact reverse played forward), `fade`, `blink`
(`step-start`, 1.1s). Hover transitions `color .15s`; accent links and dim
glyphs hover to `ink`.

## Motion

- `lib/scramble.ts` — the signature character-scramble. Per-char resolve times
  spread across ~620–800ms with 25% jitter, 15% chance right-to-left;
  unresolved chars re-randomize every 38ms from ONE of the three pools
  (`ABCDEFGHIKLMNOPRSTUVXYZ0234689`, `abcdefghikmnoprstuvxyz<>/`,
  `#%&*+=-·:;<>/`), whitespace passes through untouched. Triggers: initial
  load (staggered), clicking any leaf text element (global click handler),
  view-title change, switching chats (labels + bodies staggered 70ms/40ms),
  greeting change.
- **Streaming reply:** the same language applied to REAL streamed text — a
  ~14-char scrambled window trails the incoming tail and resolves left→right
  (advance ~5 chars per 38ms tick, never ahead of received text); a 7×14px
  blinking block cursor rides while `working`.
- Every animation is skipped under `prefers-reduced-motion: reduce` and when
  the module-level `motion` flag is off. Scramble must never corrupt final
  text: on completion the exact original string is restored.

## Layout & chrome

- **Header** ~56px, full width, 1px `line` bottom border, `padding:16px 24px`.
  Left: `←` back (surfaces only, returns to chat) OR sidebar chevron `«`/`»`
  (chat only), then the view title (13px/500 `ink-soft`, scrambles on change).
  Right: "Undo last action" (accent 12px; kept **conditional on
  hasUndoableActions** — real function) + rail chevron (chat only).
- **Main:** `display:flex; gap:44px; padding:0 40px`. Sidebar 212px, right
  rail 232px, both collapse by animating width/opacity/margin/translateX over
  .35s/.25s ease. Chat column max-width 580px (thread) / 840px (composer row).
  Threads and surfaces get the vertical fade mask
  (`mask-image: linear-gradient(180deg, transparent, #000 32px, #000
  calc(100% - 20px), transparent)`), hidden scrollbars.
- **Sidebar:** top "Workspace" block (2px rail + 11px label) with rows →
  **Tools** (mono hint: trusted-root count or mode note) and **Snapshots**
  (mono hint: restore-point count); then "＋ New chat" (accent, 12px); then
  chat groups **Today / Earlier** (real conversations bucketed by their
  timestamps): group header = 11px `faint` label + mono hint (`N chats` /
  `collapse`), click toggles; collapsed shows 3 rows + "N more…" ghost row;
  expand animates only the newly revealed rows (fadeRise .3s), collapse plays
  fadeDrop on rows beyond 3 then commits (~290ms), re-click mid-collapse
  cancels cleanly. Chat rows 12px: title ellipsized + mono time, active = 2px
  accent rail + `ink`; double-click rename SURVIVES. Footer pinned to bottom:
  "Settings" row (same rail treatment when active) + mono
  `{Simple|Developer|Custom} profile · local` note (+ ` · open` mode note).
- **Right rail** (chat only): "Addison's work" step list during a task (5px
  dots, current blinks, done rows dim `muted`, real ActivityUpdates), then
  "Save as routine" accent link (real propose-routine); hairline-separated
  widget rows (token meter "Tokens this month" with 2px track/`ink` fill —
  real stats.get; routine widgets as `name — Run` rows; stat widgets as
  name/value rows); footer "＋ Ask Addison to build a widget" (`disabled`
  color) opens the **widgets surface**. The permission/consent card and
  offer/proposal cards keep their rail/inline placement logic.
- **Mobile** (<md): keep the existing drawer + inline-widgets structure,
  restyled to tokens. Never regress touch targets below 44px.

## Screens

- **Chat empty state:** centered greeting stack — time-of-day greeting (26px
  `ink`, scramble-in; "Still up?" <5h, "Good morning." <12, "Good afternoon."
  <18, else "Good evening."), subline 14px `muted` "Ask anything, or hand me a
  chore. Everything can be undone." (fadeRise .6s delay .6s), three accent
  suggestion chips ("Tidy my Downloads folder", "Draft an email", "Plan the
  weekend") that fill the composer (fadeRise delay .9s). The prototype's faint
  dotted starfield behind it is NOT shipped — removed by owner decision
  2026-07-26. Five 1px dots over a 464x276 box never read as a field, and two of
  them landed within 20px of the type (one level with the subline), where a lone
  speck beside a word reads as a dead pixel. Reported from the running app as
  "there is a pixel or something akin to it".
  First-run: the pine banner is REPLACED by this empty state plus a
  first-run block restyled into the row idiom (setup steps as hairline rows,
  "Start setup" as an accent action; launch-only skip survives).
- **Thread:** 32px message gap; label 11px/500 ("You" `disabled` / "Addison"
  `ink`) + body 15.5px/1.65 (`ink-soft` user / `ink` assistant). Assistant
  messages keep **Markdown/Mermaid rendering**, restyled to the same type
  scale (code/tables in mono on `panel` blocks with `line` hairlines). Retry
  / rewind-to-here affordances survive as small accent/mono actions.
- **Composer** (chat only): borderless textarea (15px) over a 1px top border
  `track` → `track-hi` on focus; right: model label (mono 10.5px `disabled`,
  click → menu) + 30px circular send — idle transparent/`track` border,
  enabled accent fill + `on-accent` ↑; **while working it becomes the Stop
  control** (border circle, ■ glyph, title "Stop") — real function, not in
  the prototype. Below: mono 10px `ghost` microcopy "enter to send ·
  everything can be undone". Placeholder flips to "Addison is working…".
- **Composer model menu:** opens above the label, bottom-right anchored,
  min-width 196px, `panel` bg, `rail` border, radius 6px, "Answer with"
  header (10px), rows = mono name + mono note with ✓ + accent note on
  selected, hover row bg `line`; when the picked cloud model has effort
  levels, an "Effort" section in the same row idiom follows (real
  functionality); footer hint "picked per message · default in Settings"
  over a hairline. Model rows come from the REAL catalog (roles +
  cloudModels + local models), note = `quality`/`free`/`local` derived from
  real flags (free stays Ollama-only).
- **Surfaces** (Settings / Tools / Snapshots / Build a widget) replace the
  chat column: centered max-580px, scrollable, fade-masked; 20px title, 13px
  `muted` description, sections = 11px label on 2px `rail` rule + rows with
  1px `line` top borders. Row anatomy: name (12px `ink-soft`) — spacer —
  mono value (10.5px `muted`) — accent action link. Entering: children
  fadeRise .35s staggered 40ms; leaving: fadeDrop .25s, commit ~240ms.
  Surface state machine: `view: chat | settings | tools | snapshots |
  widgets` (previous-view tracked). Escape still returns to chat.
- **Settings sections, in order** (each backed by the REAL hook it has
  today): Where Addison thinks (role rows; cloud model + "change" → model
  popup); Which model answers (routing strategies — full picker on
  Developer/Custom, the two-option toggle rendered in the same row idiom on
  Simple; custom chain builder survives, restyled); API keys (Anthropic /
  OpenAI / Google add–remove, Your own server connect — real provider flows,
  key input stays a `panel` inline row, keys go keychain-only as today);
  Run a model on this computer (real local setup states + progress);
  Routines (real list; delete keeps danger color + confirm); Skills (real
  editor restyled); Profile (Simple ↔ Developer switch, the **Advanced…
  disclosure + two-step Custom confirm survives**, guard panel restyled into
  rows with its exact copy; Appearance row cycles Light / Dark / Match this
  computer — REAL three-way theme, default **Match this computer**);
  Folders Addison may work in (workspace trust — real rows, "choose a
  folder…" via the OS picker, Stop trusting action; Developer/Custom only,
  as today); Restore points (summary row "Going back to {target}" +
  "restore", and "All restore points / open" → the modal); Diagnostics
  (Developer raw ring preserved; Simple sees "nothing to show yet").
- **Model select popup** (Settings "change"): fixed-position anchored panel,
  270px, `panel` bg, radius 7px, hairline ring + deep shadow, fade .12s,
  positioned so the selected row sits near the click, clamped ≥12px from
  viewport edges; rows 12px with hairline separators, selected = 2px accent
  rail + bright name + accent note; click outside closes.
- **Tools surface:** "What Addison can reach on this computer." — REAL data
  only: Connected = providers with keys, trusted folders (each with revoke),
  local model when ready; Available = providers without keys ("add key" →
  Settings API-keys section). No fake IDE/Email/Calendar rows.
- **Snapshots surface** + **Restore points modal:** real restore points
  (useSnapshots). Modal: scrim + centered 440px `panel` (radius 8px, fadeRise
  .25s), header "Restore points" 15px + accent "save one now" (real
  snapshot.create) + ✕; description; hairline rows name / mono timestamp /
  accent action; footer mono note "everything can be undone · restores never
  delete your files". **Semantics that survive exactly:** the one-action
  restore = restoreLastWorking with its two-step inline confirm + consequence
  copy (restyled, not removed); per-row "Restore this one" only on PERMANENT
  rows; the Permanent tag (blocky, no Remove control); the honest
  no-verified-target sentences. "restored ✓" is shown only after a real
  successful restore.
- **Build a widget surface:** description + "Ideas to start from" rows whose
  "use" seeds the composer ("Build me a widget: …") and returns to chat —
  the existing propose→card→confirm flow is unchanged.

## Explicitly out / kept as-is

- No new backend surface, no RPC changes, no fake data anywhere.
- `FirstRunBanner`'s pine block, the serif voice, and the bundled fonts are
  retired.
- **Brand (added 2026-07-26):** the bell is retired too. The mark is the
  lowercase-a tile with the lavender dot — `brand/Addison Logo Mark.dc.html` in
  this directory is the final sheet (`Addison Logo.dc.html` is exploration
  history, `Addison Brandbook.dc.html` the wider system). Implemented as
  `shell/src/components/AddisonMark.tsx` (construction ratios + the sheet's
  exact sampled sizes; the tile stays dark in BOTH themes — fixed hex, never
  tokens), used in the header (22px, **mark only** — see the owner decision below;
  this clause read "22px + wordmark, brandbook §10" until the wordmark was dropped)
  and the first-run splash (44px above the greeting). The favicon is
  **`shell/public/favicon.png`**, a raster deliberately: the SVG version drew the
  "a" with a `<text>` element, so its letterform and size depended on whichever
  font the rasterising platform resolved (measured: a 15–20% bbox difference off
  macOS). It is rendered once from `brand/favicon-master.svg` in this directory
  and is therefore identical everywhere. **The header shows the mark ALONE** — the brandbook's APP
  HEADER application pairs it with an "Addison" wordmark, but that is redundant
  in the app's own chrome and it spent the view title's width budget (owner
  decision 2026-07-26). The **Tauri OS icon set is regenerated** from the mark
  (`shell/src-tauri/icons/`, 2026-07-26): master at
  `docs/design-brief-dark/brand/app-icon.svg`, built on Apple's macOS grid (an
  824×824 tile centred in 1024 with 100px margins) at the DOCK panel's larger
  ~22.5% corner radius, rasterised through QuickLook and `sips`, `.icns` via
  `iconutil`, `.ico` as a PNG-embedded container. Regenerate with
  `docs/design-brief-dark/brand/build-app-icon.sh`.
- All permission/consent flows, offers (endpoint/cost-plan), widget/routine
  proposal cards: same logic, restyled (hairline rows on `panel`/flat, accent
  actions, mono values).
- Tests are UPDATED to the new design honestly — never deleted to go green;
  behavior assertions (confirm flows, honesty copy, G-floor guards) keep
  their teeth.

## What the build learned after this brief was written (2026-07-26)

This file was written on 2026-07-25 as the mapping to build *from*. The sections
above still describe the intended design; the items below are things the build
settled that the brief did not anticipate, recorded here so the next reader is not
mapping against a stale picture. None of them change the design language.

- **The light ramp is contrast-bound, not a straight translation.** See the note
  under the token table. This is the largest single correction to this document.
- **The composer scrolls in a reserved lane.** The prototype hides every scrollbar,
  and the app keeps that for reading columns (`.no-scrollbar`). The composer's
  textarea is the one scroller a person drags, so it gets a **bespoke** thumb
  (`.bespoke-scroll`, its own `scrollbar` / `scrollbar-hi` tokens) in a reserved
  gutter clear of the text. Deliberately no `scrollbar-width` / `scrollbar-color`:
  once the standard property is set, Chromium and Safari 18+ ignore the
  `::-webkit-scrollbar` rules entirely. The composer's text also wraps full-width
  over a controls strip, and its max-height lands on the line grid.
- **A finished answer is revealed with the scramble, not shown whole.** The same
  motion language covers the case the brief only described for streaming: the first
  frame is emitted **synchronously**, in the same commit that sets the target, so
  the finished text is never painted before being hidden. The reveal **rate adapts**
  to the answer's length (`revealAdvanceFor`) — at a fixed rate a long answer is not
  an animation, it is a wait — and it can never display text that has not been
  committed. `onDone` fires exactly once and a finished engine is not reused.
- **The view title is not the only thing that scrambles on a switch.** The sidebar
  title scrambles on chat switch too; the stagger survives remounts and is capped at
  the viewport, and adopting the launch conversation's id does not replay it.
- **Motion has a cleanup and a scroll contract.** The animation path cleans up on
  unmount, and a reveal never scroll-jails the thread — auto-scroll follows only a
  reader already at the bottom.
- **The restore modal's footer claim is mode-scoped.** "everything can be undone ·
  restores never delete your files" is true under SAFE; under OPEN it reads "some
  actions can't be undone · …", because `run_command` is the undo rule's one
  explicit exemption and a footer that contradicts the profile card is exactly the
  quiet over-promise this floor cannot afford. The restores half never changes. An
  absent mode is treated as `safe`.
- **The one-action restore names its target and timestamp *through* the confirm.**
  This regressed against master during the redesign and was restored; the modal also
  moves, traps and restores focus.
- **Chrome fixes the brief did not foresee.** A pending consent card is hoisted
  above the modal/drawer scrim that made it unanswerable, and the modal's focus trap
  deliberately includes it. Collapsed columns are `inert` rather than holding
  invisible focus stops. Effort is keyboard-reachable. The rail sheds before the
  reading column is squeezed. Row names truncate; rows carrying sentences wrap.
  Routines no longer claim "None yet" while the engine is unreachable. Consent
  answers have real hit targets, with Allow dominant by **fill** rather than hue.
- **The favicon is a raster on purpose** (already recorded above): the SVG drew the
  "a" with `<text>`, so its letterform depended on whichever font the rasterising
  platform resolved — measured at a 15–20% bbox difference off macOS.
- **Two surfaces have grown since (2026-08-06), and the sections above do not list
  the additions.** Settings gained a **Tool servers** section (Developer/Custom only,
  between "Folders Addison may work in" and "Restore points") for the MCP client's
  configuration; the right rail gained three **interactive** widget kinds — a
  checklist you tick, a note you edit and a timer you start and pause — beside the
  routine and stat rows. Both use the existing row idiom and neither changes the
  design language; `../../ROADMAP.md` owns their status.
- **The two model lists are a FOLDER TREE, and that is a deviation from the
  prototype (2026-08-07, owner decision).** §8 and §9 of the designer's reference draw
  a flat list of rows, which was right for the five models the app had; a single
  connected Google key now contributes twenty-two, and a panel that shows all of them
  is not a menu, it is a scroll. So the Settings popup and the composer menu both draw
  company → family → model, **one folder open at a time**, from one engine
  (`shell/src/lib/modelGroups.ts`) so the two cannot disagree. Everything else in §8
  and §9 is unchanged — the geometry, the hairline rows, the accent rail on the
  selected row, the notes, the footer hint. Three consequences worth writing down:
  the popup's vertical placement is **measured** rather than computed from a row
  index, because folders sit between the top of the panel and the selected row and
  index × row-height stopped describing anything; each panel opens with the model in
  effect already revealed, which is what keeps the macOS-select promise a folder tree
  could otherwise break; and the rows are a `role="tree"` with the WAI-ARIA keyboard,
  one tab stop for the whole list, with a single deliberate departure — Up and Down
  **wrap**, because that is what a menu does. **This supersedes the sidebar's "3 rows
  + N more…" preview idiom for these two panels only**; §4's chat list keeps it, where
  a preview is still right because those rows have no hierarchy to stand for them.
