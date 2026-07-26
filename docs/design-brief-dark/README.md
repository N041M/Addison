# Handoff: Addison — desktop app redesign

> **How to read this file (added 2026-07-26).** This is the **designer's handoff,
> reproduced as delivered** — it is the authoritative reference for colours, type,
> spacing, copy and motion, and it is deliberately not edited to match the app. The
> binding prototype→app mapping lives beside it in **`IMPLEMENTATION.md`**, and
> where the two differ on anything other than visual intent, `IMPLEMENTATION.md`
> wins. Three standing translations, so nothing below is read as a contract:
>
> - **"dark theme only" describes the designed reference, not the shipped app.**
>   Dark is the reference; light is a derived translation with the same structure,
>   and the theme stays class-driven three-way (light / dark / system, default
>   **system**). The light values are contrast-bound rather than translated by eye —
>   see the note under `IMPLEMENTATION.md`'s token table.
> - **All content here is demo data.** The sample chats, connections and restore
>   points are the prototype's; the app renders real state and never ships a control
>   that displays fabricated state.
> - **The app has features this prototype does not show** (Stop while working, the
>   Custom profile and guard panel, workspace trust, offer/proposal cards, markdown
>   and mermaid rendering, the restore confirm flow, mobile layout). They are
>   restyled into this language, never deleted and never de-wired.
>
> The prototype file is checked in here as **`prototype.html`** (with `support.js`
> beside it); the "Files" section at the bottom names it by its original delivery
> filename, `Addison Prototype v2.dc.html`.

## Overview
A redesign of Addison, a local-first AI assistant desktop app (macOS). Core ideas: calm, text-first dark UI; everything reversible ("everything can be undone"); a left sidebar of chats, a center chat column, a right rail of ambient widgets, and full-page surfaces (Settings, Tools, Snapshots, Build-a-widget) that replace the chat column. Signature motion: a character-scramble text animation and small fade/rise transitions.

## About the Design Files
The files in this bundle are **design references created in HTML** — a working prototype showing intended look and behavior, not production code to copy directly. Recreate this design in the target codebase's existing environment (Electron/React, Swift/AppKit, etc.) using its established patterns. If no environment exists yet, choose the stack that fits a macOS-style desktop app and implement the design there.

- `Addison Prototype v2.dc.html` — the full prototype (open in a browser; `support.js` must sit next to it). All layout/styling is inline in the markup; all behavior is in the `<script data-dc-script>` class at the bottom.

## Fidelity
**High-fidelity.** Colors, type, spacing, copy, and motion are final intent. Recreate pixel-perfectly; where the real app already has equivalents (e.g. native menus), match the prototype's styling.

## Design Tokens
Colors (dark theme only):
- App background: `#0C0C0D`
- Panel / popover background: `#141518` (model popup: `#141518`, composer menu: `#141518`)
- Hairline borders / separators: `#1E1F22` (popover hairline: `rgba(255,255,255,.07)` ring instead of a border)
- Section rail (inactive): `#2E2F33`
- Accent (primary, purple): `#B4A9F5`; text-on-accent: `#100E22`
- Text primary: `#E9E9E7`; secondary: `#B9BBBE`; muted: `#909398`; faint: `#6E7076`; disabled/hints: `#55575C`; ghost: `#3C3E42`
- Overlay scrim: `rgba(0,0,0,.55)`

Typography:
- UI: `'Helvetica Neue', Helvetica, Arial, sans-serif`
- Mono (metadata, hints, timestamps, values): `ui-monospace, 'SF Mono', Menlo, monospace`
- Sizes: page/surface title 20px; greeting 26px; header title 13px/500; body message 15.5px, line-height 1.65; rows & labels 12px; section labels 11px/500 with `.04em` letter-spacing; mono meta 10–10.5px
- Links/actions: purple `#B4A9F5`, hover `#E9E9E7`, `transition: color .15s`

Other:
- Radii: popovers/modals 7–8px; send button circle 50%; menu rows 4–5px
- Shadows: popover `0 0 0 .5px rgba(255,255,255,.07), 0 18px 48px rgba(0,0,0,.65)`; modal `0 24px 64px rgba(0,0,0,.6)`
- Selection/active marker: 2px left rail in accent purple (chat rows, model rows, settings nav)
- Spacing: main columns gap 44px; page padding 0 40px; sidebar 212px, right rail 232px; chat column max-width 580–840px

## Screens / Views

### 1. Chat — empty state
Centered greeting stack: time-of-day greeting (26px, `#E9E9E7`, scramble-in), subline "Ask anything, or hand me a chore. Everything can be undone." (14px `#909398`), and 3 purple suggestion chips ("Tidy my Downloads folder", "Draft an email", "Plan the weekend") that fill the composer. A faint dotted "starfield" (few 1px radial-gradient dots, some purple) sits behind. **[NOT SHIPPED — owner decision 2026-07-26: with five dots it read as dust rather than a field, and two landed within 20px of the type. See IMPLEMENTATION.md.]** Sublines fade-rise in staggered (.6s ease, delays .6s/.9s).

### 2. Chat — thread
Single centered column (max 580px), messages stacked with 32px gap, vertical fade mask top/bottom. Each message: tiny label ("You" `#55575C` / "Addison" `#E9E9E7`, 11px/500) + body (15.5px; user `#B9BBBE`, assistant `#E9E9E7`, `white-space: pre-wrap`). While replying, a 7×14px blinking block cursor follows the text.

### 3. Composer (chat only)
Bottom of center column: borderless textarea (15px) over a 1px top border that brightens on focus; right side has the model label (mono 10.5px `#55575C`, click → model menu) and a 30px circular send button (idle: transparent + `#55575C` ↑; enabled: purple fill, dark glyph). Below: mono microcopy "enter to send · everything can be undone".

### 4. Left sidebar (212px, collapsible)
"＋ New chat" (purple, 12px); chat list grouped **Today / Earlier**. Group header row: label (11px `#6E7076`) + mono hint (count, or "collapse"); click toggles. Collapsed groups show 3 rows + "N more…" row. Chat rows: 12px, title ellipsized + mono timestamp; active row = purple 2px left rail + `#E9E9E7`. Footer pinned to bottom: "Settings" row (same rail treatment when active) + mono profile note "Simple profile · local". Sidebar collapses via header « chevron (width/opacity/translate animate .35s).

### 5. Right rail — widgets (232px, collapsible, chat view only)
"Addison's work" step list during a task (5px dots; current step blinks, done steps dim). Widget cards separated by hairlines; footer row "Build a widget" (`#55575C`) opens the widget surface. Rail hides entirely on surface views; header » chevron toggles it in chat.

### 6. Header
56px-ish bar, 1px bottom border. Left: back arrow ← (surfaces only, returns to chat) OR sidebar chevron (chat only) + view title (scrambles on change). Right: "Undo last action" (purple) + rail chevron (chat only).

### 7. Surfaces (Settings / Tools / Snapshots / Build a widget)
Replace the chat column (centered, max 580px, scrollable, fade-masked). Pattern: 20px title, 13px `#909398` description, then sections: label with 2px `#2E2F33` left rail, rows separated by 1px `#1E1F22` top borders. Row anatomy: name (12px `#B9BBBE`) — spacer — mono value (10.5px `#909398`) — purple action link.

**Settings sections** (in order): Where Addison thinks (Cloud default ✓ / On this computer "not set up yet" / Cloud model + "change" → model popup); Which model answers (Quality first / Cost first / Local only / Custom order — single-select, "selected ✓"); API keys (Anthropic/OpenAI/Google add–remove, Your own server "OpenAI-compatible · http://…" connect); Run a model on this computer (Light and quick 2 GB/8 GB, Balanced 4.7 GB/16 GB, Most capable 9 GB/32 GB — "set up" → "ready ✓"); Routines (empty state); Skills (empty state, "add a skill"); Profile (Simple ↔ Developer switch, Appearance cycles Light/Dark/Match this computer, "What Addison calls you"); Folders Addison may work in ("choose a folder…"); Restore points (opens Restore points modal); Diagnostics ("nothing to show yet").

Sidebar footer note mirrors profile: "{Simple|Developer} profile · local".

### 8. Model select popup (from Settings "change")
Anchored floating panel near the clicked action (fixed-position; appears so the selected row sits near the cursor, macOS-select style). 270px wide, bg `#141518`, radius 7px, hairline ring + deep shadow, fade-in .12s. Rows (12px, separated by `#1E1F22` hairlines): model name left, mono role note right ("quality"/"free"/"local"). Selected row: purple 2px left rail, bright name, purple note. Hover brightens text. Click outside closes.

### 9. Composer model menu
Same family: opens above the model label (bottom-right anchored, min-width 196px, radius 6px, "Answer with" header 10px, rows name+note with ✓ and purple note on selected, hover row bg `#1E1F22`), footer hint "picked per message · default in Settings" over a hairline.

### 10. Restore points modal ("new window" style)
Full-screen scrim `rgba(0,0,0,.55)` (fade .2s); centered 440px panel (bg `#141518`, radius 8px, shadow, fade-rise .25s). Header: "Restore points" (15px) + purple "save one now" + ✕. Description line, then hairline rows: name / mono timestamp / purple "restore" → "restored ✓". Footer mono note "everything can be undone · restores never delete your files". Click scrim or ✕ closes; clicks inside don't propagate.

## Interactions & Behavior

**Scramble text animation (signature).** Text resolves from random glyphs: each character gets a resolve time spread across ~620–800ms (25% jitter, occasionally right-to-left), unresolved chars re-randomize every 38ms tick from one of three pools (`ABCDEFGHIKLMNOPRSTUVXYZ0234689`, `abcdefghikmnoprstuvxyz<>/`, `#%&*+=-·:;<>/`); whitespace never scrambles. Triggers: initial load (staggered by element), clicking any leaf text element, view-title changes, switching chats (message labels + bodies, staggered 70ms/40ms), greeting changes. Skipped when reduced-motion or the `motion` flag is off.

**AI reply "typing".** Same scramble language, streaming: a ~14-char scrambled window advances left→right at 5 chars per 38ms tick; text behind the window is resolved, whitespace passes through. Blinking cursor while working. Work steps appear in the right rail during the reply ("Reading your request" → "Gathering what I need" → "Writing answer"), each blinking until done.

**Keyframes.** `fadeRise` (opacity 0→1, translateY 6px→0), `fadeDrop` (exact reverse, played forward so tempo matches), `fade`, `blink` (step 1.1s).

**Sidebar group expand/collapse.** Expand: only newly revealed rows `fadeRise .3s ease`. Collapse: rows beyond the first 3 play `fadeDrop .3s ease`, then are removed (state commits after ~290ms). Kept rows never re-animate. Re-clicking mid-collapse cancels it (rows fade back in); rapid toggles must not queue stale state.

**View transitions.** Opening a surface: its children (title, description, each section) `fadeRise .35s` staggered 40ms. Leaving a surface (back arrow, sidebar toggle, or switching surfaces): children `fadeDrop .25s`, state commits at 240ms. Sidebar workspace items toggle: clicking the active one returns to chat.

**Menus/popovers.** Open on click of their trigger; close on outside click, on selection, or re-click of trigger. Model popup positions itself so the selected row aligns with the click point, clamped ≥12px from viewport edges.

**Chrome rules.** Right rail + its header chevron and the sidebar chevron are chat-only; surfaces show the ← back arrow instead. Rail/sidebar collapse animates width, opacity, margin, and translateX over .35s/.25s ease.

**Hover states.** All purple actions and `#55575C` icons → `#E9E9E7` (.15s). Menu rows brighten text (settings-style lists) or fill `#1E1F22` (composer menu).

## State Management
- `chats` (id → {title, time, group, messages[], steps[]}), `order`, `activeId`
- `draft`, `working` (send disabled + placeholder "Addison is working…"; chat switching blocked while working)
- `view`: `chat | settings | tools | snapshots | widgets`; previous-view tracking for transitions
- `expanded` per chat group; pending-collapse timer with cancel
- `railOpen`, `sideOpen`, `focused` (composer border)
- `modelIdx` (shared by composer + settings), `modelMenuOpen` (composer), `modelPick` {x,y} (settings popup anchor)
- `snapModal`, `snapRestored` per restore point
- Settings: `profile` (Simple/Developer), `appearance` (Light/Dark/Match this computer), `answerPolicy` (Quality first/…), `keys` {anthropic, openai, google, server}, `localSetup` {light, balanced, capable}, `toolConn` {email, browser}
- A `motion: boolean` flag disables all animation (accessibility; also respect `prefers-reduced-motion`)

## Assets
None — no images or icon fonts. Arrows/chevrons are text glyphs (←, «, », ＋, ↑, ✕, ✓, ·). The empty-state starfield is a few radial-gradient dots. **[NOT SHIPPED — see line 63 and IMPLEMENTATION.md; removed 2026-07-26.]**

## Files
- `Addison Prototype v2.dc.html` — complete prototype: markup + inline styles in `<x-dc>`, behavior in the `Component` class (scramble engine ~line 358, send/streaming ~line 404, view/collapse transitions ~line 280–335, all screen content in `renderVals()`).
- `support.js` — prototype runtime only (renders the file in a browser); not part of the design. One copy, at the root of this directory. `brand/` used to carry a byte-identical second copy; that was deleted 2026-07-26 and the three `brand/*.dc.html` files now load `../support.js`.
