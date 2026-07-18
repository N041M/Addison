# Handoff: Addison UI Redesign ("Fern")

## Overview
Complete redesign of the Addison desktop app (repo: `N041M/Addison`, Tauri + React + Tailwind in `shell/`). Replaces the current cool-slate single-window chat with a warm, calm three-column layout: conversation sidebar, correspondence-style chat, and a user-owned widget rail. Adds an in-window settings page, multi-provider API keys, dark mode, a first-run setup banner, and a service-bell logo.

## About the Design Files
The `.dc.html` files in this bundle are **design references created in HTML** — interactive prototypes showing intended look and behavior, NOT production code to copy. Open them in a browser (keep `support.js` beside them; they fetch Google Fonts, so go online). The task is to **recreate these designs inside the existing codebase** (`shell/src`, React + Tailwind + Tauri), using its established patterns: the typed `ipc` client, the existing notification subscriptions, the permission/undo wiring. Do not change the safety model (permission gate, undo-at-registration, key isolation, no-arbitrary-shell).

- `Addison App.dc.html` — **the primary reference.** Interactive: sidebar Settings ↔ chat routing, widget-rail hide/show, widget tray expand, light/dark toggle (Settings → Appearance).
- `Addison Brand Book.dc.html` — tokens, type, shape rules, component specs, implementation order.
- `Addison Logo.dc.html` — logo sheet; **concept B (service bell) is chosen.**
- `Addison Redesign Directions.dc.html` — exploration history (context only; the app file wins on conflicts).

## Fidelity
**High-fidelity.** Colors, type, spacing, radii, and copy are final; recreate pixel-perfectly with Tailwind against the token set below. The widget contents (token counts, latency) are sample data.

## Design Tokens

### Color — light
| token | hex | use |
|---|---|---|
| paper | `#F6F5F1` | app background |
| side | `#F2F1EC` | sidebar background |
| surface | `#FDFCFA` | cards, composer |
| line | `#E3E1D8` | 1px borders |
| hair | `#ECEAE2` | active sidebar item bg, progress track |
| ink | `#22261F` | primary text |
| ink-soft | `#4D5147` | secondary text |
| muted | `#6B6F64` | tertiary text |
| faint/dim | `#9A9D92` | labels, placeholders |
| fern (accent) | `#33604F` | primary buttons, Addison's voice, live dots |
| fern-deep | `#2A5142` | accent text/links, hover |
| fern-tint | `#E4EDE8` | consent card bg, selected states, pill bg |
| rule | `#D8E5DE` | 2px "Addison's work" left rule |
| dash | `#C9CCBE` | dashed "add widget" border |
| pine | `#1E2B25` | setup banner bg (only high-contrast block) |
| danger | `#8F3A44` | errors (unchanged from current app) |

### Color — dark (`darkMode: "class"`)
paper `#171D1A` · side `#131815` · surface `#1F2723` · line `#2E3A33` · hair `#243029` · ink `#E7EBE7` · ink-soft `#C3CDC6` · muted `#92A099` · dim `#6F7D75` · fern `#7FB59A` · fern-text `#A9CBB9` · on-accent `#14201A` (dark text on light-fern buttons) · tint `#223229` · rule `#33493D` · dash `#3B4A41`. Same hues, inverted values; primary buttons flip to dark-on-light-fern.

### Typography
- **Source Serif 4** (500 display / 400 text) — message text 17px/1.7; greetings 32px; settings/section display. This serif = the "correspondence" feel.
- **Public Sans** — all UI. Body 15px/1.55, controls 13–13.5px/500–600, small-caps section labels 10.5px/600, uppercase, letter-spacing .09–.11em.
- **IBM Plex Mono** — machine facts only (token counts, latency, model tags, diagnostics), 10–12px.
- **CSP note:** don't hotlink Google Fonts — bundle woff2 files in `shell/src/assets/fonts/` with `@font-face` (all three are OFL). Keep system-stack fallbacks.

### Shape ("blocky = live annotation, rounded = ownable/actionable")
- Blocky (square, 2px left rules, small-caps labels): "Addison's work" list, active sidebar item indicator, section labels. The rule spans **only its list**, never the whole column.
- Rounded: 6px small buttons/selects · 8px inputs/rows · 10px cards/composer · 12px banners · 999px pills (Run, Allow, tray runs).
- Shadows: `0 1px 4px rgba(34,38,31,.05)` on composer/cards; pine banner `0 6px 18px rgba(30,43,37,.18)`; nothing else.

## Screens / Views

### 1. App shell (3 columns)
- **Sidebar 216px**, `side` bg, right `line` border. Top: bell logo (17px, fern) + "Addison" (Public Sans 700 16px, -0.02em) + collapse `«`. "＋ New chat" outlined button (6px radius, fern-deep text). Sections "TODAY"/"EARLIER" (small-caps labels). Chat items: 13px, 8px/16px padding, square with 2px left fern bar + `hair` bg when active, ellipsis overflow. Bottom: Settings item (same active treatment on settings screen) + "Simple profile" 12px dim.
- **Chat column** flex, max 580px, own scroll (hidden scrollbar).
- **Widget rail 270px**, own scroll (thin scrollbar), hideable.

### 2. Chat
- Header row: chat title 13px/600 ink-soft; right: "↺ Undo last action" (muted text button) and rail toggle "Hide widgets »" / "« Show widgets" (fern-deep text button). Bottom `line` border.
- Messages: no bubbles. Small-caps sender label (YOU = dim, ADDISON = fern-deep) above Source Serif 17px/1.7 text. 26px gap between turns. Hover on a user turn reveals "Rewind to here" (existing behavior, keep).
- Composer: `surface` card, 10px radius, 1px `line`, soft shadow; textarea placeholder "Write to Addison…"; bottom row = model pill text button "Claude Opus 4.8 · thorough ▾" (muted) left, fern "Send" button (6px radius, 9px 26px padding) right. Below: 12px dim hint "Press Enter to send… anything it does can be undone."

### 3. Widget rail
- Header: "YOUR WIDGETS" small-caps + "Edit" text button.
- Widget cards (`surface`, 1px `line`, 10px radius, 11px 13px padding): routine row (name + fern-tint pill "Run"), token meter (small-caps title + mono value + 5px progress bar, fern on `hair`), connections list (6px status dots: fern = up, dash-gray = idle; mono right-aligned values).
- Overflow tray: stacked-edge tab (two offset 8px strips peeking above a full row button "4 more widgets ▾"); expanding reveals extra rows inline, label flips to "Show fewer ▴". Pinned state (⬤/◯) per widget in expanded view.
- "＋ Ask Addison to build a widget" — dashed border (dash token), 10px radius, muted text; widgets are support scripts Addison writes in chat and the user pins.
- **"Addison's work"** block below widgets: 2px `rule` left border + 14px padding-left wrapping ONLY the small-caps label, dot list (filled fern dot = done, 1.5px outlined = in progress, 12.5px ink-soft text), and underlined "Save these steps as a routine" link. Ends where the list ends.
- **Consent card** below the work block, outside the rule: fern-tint bg, 10px radius, question 12.5px/600 ("Allow Addison to save "Weather note.txt" in Documents?"), consequence line 11.5px ("One new file. You can undo it afterwards."), pill "Allow" (fern, white text) + "Not now" text button. When the rail is hidden, render it inline in the thread instead.

### 4. Settings (in-window page, not a drawer)
Two independent flex columns (16px gap), cards flow down each column (no row grid). Header: "Settings" + "Back to chat".
- **Column A:** "Where Addison thinks" (two selectable rows, selected = fern border + tint bg + ✓; note that cloud models come from API keys) → "API keys" → "Routines" (rows: name + run-count line, fern-tint "Run" pill + "Remove" text).
- **Column B:** "Run a model on this computer" (Ollama line with "what's Ollama?" underline link; three model rows: name, "2 GB download · needs 8 GB memory" dim line, outlined "Set up" or "✓ On this computer") → "Profile" (Simple/Developer segmented control, safety-identical description, Appearance Light/Dark segmented control below a `hair` divider).
- **API keys card:** provider rows (8px radius): Anthropic saved ("✓ Key saved · added Jul 12" in fern-deep, Replace/Remove), OpenAI expanded (fern border, password input + Save, "Checked with one tiny request…" note), Google (outlined "Add key"), "Your own server" (mono `OpenAI-compatible · http://…`, "Connect"). Footer: models from every connected provider appear together in the picker.

### 5. First run (see directions file, option 5a)
Pine banner in the chat column: small-caps "FIRST-TIME SETUP · STEP 1 OF 2" (#8FB5A2), serif headline "Let's get Addison ready." (#F4F6F3), numbered steps (cream filled circle = current, outlined = later), cream button "Start setup", "Skip for now" text. Shown only while `roles.every(r => !r.configured)`; hands off to the existing Setup Assistant; never shown again once configured. Below: serif greeting "Good afternoon." + one-line promise. Rail shows empty-state Connections card + dashed add-widget button.

## Interactions & Behavior
- Sidebar Settings ↔ chat: in-window routing, no drawer/scrim.
- Rail toggle: header button flips "Hide widgets »"/"« Show widgets"; persist in localStorage (like `addison.defaultRole`). Consent prompts move inline when hidden.
- Tray expand/collapse; pin toggles; persist pinned set + order.
- Theme: Settings → Appearance; class toggle on root; persist; 250ms background/color transition; respect `prefers-reduced-motion` (existing rule).
- All existing behaviors keep their wiring: streamed text into pending message, Stop, Retry, Rewind-to-here, permission respond, undo/redo detail lines, routine propose/confirm, local-model setup progress bar (restyle: 5px fern bar on `hair`).

## State Management
Existing App state stays. New: `screen` ("chat"|"settings"), `railOpen`, `trayOpen`, `pinnedWidgets[]`, `theme` ("light"|"dark"), `conversations[]` + `activeConversationId` (needs a conversations table in the SQLite schema — currently single-thread). Widgets are declarative JSON `{id, title, valueSource, action?}`; Addison proposes them like routines (gated, saved only on confirm); token/latency widgets need a new `stats.get` IPC method.

## Assets
- Logo: service bell, inline SVG (3 shapes), in `Addison Logo.dc.html` and the app sidebar. Monochrome only: fern on light, `#E9EDE9` on pine/dark. Use for favicon/tray at 16px.
- Fonts: Source Serif 4, Public Sans, IBM Plex Mono (bundle locally, OFL).
- No raster assets.

## Suggested order of work
1. Tailwind tokens + bundled fonts (pure config — whole app reskins)
2. Settings page (moves drawer content in-window; add API-keys card)
3. Three-column shell + sidebar (needs conversations schema)
4. Rail: Addison's-work list + consent card (rewire ActivityPanel/PermissionCard feeds)
5. Widgets + `stats.get` IPC + tray/pinning
6. Dark mode
7. First-run pine banner + bell favicon

## Files
- `Addison App.dc.html` — primary interactive reference (chat, rail, settings, themes)
- `Addison Brand Book.dc.html` — tokens & rules (its "Implementation" section maps component-by-component to `shell/src`)
- `Addison Logo.dc.html` — logo sheet (B chosen)
- `Addison Redesign Directions.dc.html` — exploration history (first-run 5a, dark 5c, tray 9a)
- `support.js` — runtime the HTML references; needed only to open the files, not for implementation
