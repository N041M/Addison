/** @type {import('tailwindcss').Config} */
// Addison's visual direction is binding: the DARK direction (docs/design-brief-dark,
// 2026-07-25) — a calm, text-first surface with warm-neutral greys, one soft
// violet accent, and system type only. It SUPERSEDES the Fern direction
// (docs/design-brief-fern, which stays in the tree as history).
//
// The shape language changed with it: selection/active is a 2px accent LEFT RAIL
// (chat rows, model rows, settings nav); section labels sit on a 2px `rail` left
// rule; hairline `line` top-borders separate rows. Cards-with-borders are now
// reserved for floating chrome (popovers, modal, composer menu) — surfaces are
// flat hairline rows, not floating cards.
//
// Colors are driven by CSS custom properties (channels declared in
// src/styles.css: :root = light, .dark = dark) so the whole theme flips with one
// class on <html>. darkMode:"class" stays; the class is toggled from Settings →
// Appearance and persisted in localStorage ("addison.theme"), whose default is
// now "system" (Match this computer).
//
// Dark is the DESIGNED reference (exact hex from the handoff); light is a derived
// translation with identical structure. docs/design-brief-dark/IMPLEMENTATION.md
// holds the authoritative token table.
const withOpacity = (v) => `rgb(var(${v}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // --- The dark-direction palette (IMPLEMENTATION.md token table) -----
        paper: withOpacity("--c-paper"), // app background
        panel: withOpacity("--c-panel"), // popovers, menus, modal
        line: withOpacity("--c-line"), // 1px hairlines / separators
        rail: withOpacity("--c-rail"), // inactive 2px section rails; menu borders
        track: withOpacity("--c-track"), // composer idle top border, meter track
        "track-hi": withOpacity("--c-track-hi"), // composer focused top border
        ink: withOpacity("--c-ink"), // primary text
        "ink-soft": withOpacity("--c-ink-soft"), // secondary text
        muted: withOpacity("--c-muted"), // tertiary text
        faint: withOpacity("--c-faint"), // section labels
        disabled: withOpacity("--c-disabled"), // hints, idle glyphs, "You" label
        ghost: withOpacity("--c-ghost"), // faintest microcopy
        accent: withOpacity("--c-accent"), // links/actions, selection rails, send fill
        "on-accent": withOpacity("--c-on-accent"), // glyph on an accent fill
        // Real destructive actions only (delete a widget/routine). A RESTORE is
        // never danger-colored — it is a recovery, not a loss (HANDOFF rule).
        danger: withOpacity("--c-danger"),
        // Overlay scrim behind the modal — the same value in both themes.
        scrim: "rgb(var(--c-scrim) / 0.55)",
      },
      fontFamily: {
        // System stacks only — every bundled OFL woff2 and @font-face is gone,
        // and so is the serif voice: there is no `font-serif` token to reach for.
        sans: ['"Helvetica Neue"', "Helvetica", "Arial", "sans-serif"],
        mono: ["ui-monospace", '"SF Mono"', "Menlo", "monospace"],
      },
      // No named fontSize scale: the dark direction's type scale is expressed as
      // literal px utilities (text-[15.5px], text-[11px], …) — the prototype's
      // inline sizes, which is what "pixel-perfect" means here.
      letterSpacing: {
        // The surface-title and greeting tightening the brief calls for. The only
        // named tracking the design has; everything else is a literal.
        display: "-0.01em",
      },
      borderRadius: {
        // Dark direction: popover 7px, modal 8px, composer menu 6px, menu rows
        // 4–5px, send button 50%. No pills, no 10–12px cards.
        menu: "5px",
        popover: "7px",
        modal: "8px",
      },
      boxShadow: {
        // Floating chrome only. Both flip with the theme (values in styles.css).
        popover: "var(--shadow-popover)",
        modal: "var(--shadow-modal)",
        // The composer's model menu (design-brief-dark: 0 12px 32px rgba(0,0,0,.5)).
        menu: "var(--shadow-menu)",
      },
    },
  },
  plugins: [],
};
