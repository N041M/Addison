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

        // --- TEMPORARY legacy aliases --------------------------------------
        // TODO(redesign phase 4): remove legacy aliases.
        // Phases 2–3 restyle ChatThread/Composer/SettingsPage and the cards; until
        // then those components still name Fern tokens. Each alias points at its
        // nearest dark-direction equivalent so the app keeps compiling AND keeps
        // rendering in the new palette (never a half-old one) while the wave lands.
        fern: withOpacity("--c-accent"),
        "fern-deep": withOpacity("--c-accent"),
        "fern-tint": withOpacity("--c-panel"),
        surface: withOpacity("--c-panel"),
        side: withOpacity("--c-paper"),
        hair: withOpacity("--c-line"),
        rule: withOpacity("--c-rail"),
        dash: withOpacity("--c-rail"),
        // The pine first-run block is retired by the brief; its tokens survive
        // only so FirstRunBanner keeps rendering until phase 2 replaces it.
        pine: withOpacity("--c-panel"),
        "pine-soft": withOpacity("--c-muted"),
        "pine-ink": withOpacity("--c-ink"),
        cream: withOpacity("--c-ink"),
        "pine-body": withOpacity("--c-ink-soft"),
        "pine-muted": withOpacity("--c-muted"),
        "pine-line": withOpacity("--c-rail"),
        notice: withOpacity("--c-muted"),
        "notice-tint": withOpacity("--c-panel"),
      },
      fontFamily: {
        // System stacks only — every bundled OFL woff2 and @font-face is gone.
        sans: ['"Helvetica Neue"', "Helvetica", "Arial", "sans-serif"],
        mono: ["ui-monospace", '"SF Mono"', "Menlo", "monospace"],
        // TODO(redesign phase 4): remove legacy aliases.
        // The serif voice is retired. `font-serif` survives as an alias to the UI
        // stack so the not-yet-restyled components (ChatThread, FirstRunBanner,
        // SettingsPage) render in the new type language rather than a leftover
        // serif; phases 2–3 delete the classes, phase 4 deletes this line.
        serif: ['"Helvetica Neue"', "Helvetica", "Arial", "sans-serif"],
      },
      fontSize: {
        // TODO(redesign phase 4): remove legacy aliases.
        // The dark direction's type scale is expressed as literal px utilities
        // (text-[15.5px], text-[11px], …) — the prototype's inline sizes, which
        // is what "pixel-perfect" means here. These named Fern sizes stay only
        // for components phases 2–3 have yet to restyle.
        tag: "9.5px",
        tick: "10px",
        label: "10.5px",
        fact: "11px",
        fine: "11.5px",
        hint: "12px",
        meta: "12.5px",
        control: "13px",
        action: "13.5px",
        row: "14px",
        body: "15px",
        message: "17px",
        glyph: "19px",
        title: "20px",
        headline: "24px",
        greeting: "26px",
      },
      letterSpacing: {
        // `display` (-0.01em) survives the redesign: it is the surface-title and
        // greeting tightening the brief calls for.
        display: "-0.01em",
        // TODO(redesign phase 4): remove legacy aliases (logo/emphasis/caps*).
        logo: "-0.02em",
        emphasis: "0.02em",
        caps: "0.06em",
        "caps-wide": "0.09em",
        "caps-wider": "0.11em",
      },
      borderRadius: {
        // Dark direction: popover 7px, modal 8px, composer menu 6px, menu rows
        // 4–5px, send button 50%. No pills, no 10–12px cards.
        menu: "5px",
        popover: "7px",
        modal: "8px",
        // TODO(redesign phase 4): remove legacy aliases (sm = the 6px composer
        // menu radius, DEFAULT = the 8px modal radius — kept for the components
        // phases 2–3 still own).
        sm: "6px",
        DEFAULT: "8px",
        card: "10px",
        banner: "12px",
        pill: "999px",
      },
      boxShadow: {
        // Floating chrome only. Both flip with the theme (values in styles.css).
        popover: "var(--shadow-popover)",
        modal: "var(--shadow-modal)",
        // The composer's model menu (design-brief-dark: 0 12px 32px rgba(0,0,0,.5)).
        menu: "var(--shadow-menu)",
        // TODO(redesign phase 4): remove legacy aliases.
        soft: "var(--shadow-popover)",
        banner: "var(--shadow-popover)",
      },
    },
  },
  plugins: [],
};
