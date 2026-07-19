/** @type {import('tailwindcss').Config} */
// Addison's visual direction is binding: the "Fern" direction — warm paper
// neutrals + one fern-green accent, a serif "correspondence" voice (Source Serif
// 4) beside a plain sans UI (Public Sans) and mono for machine facts (IBM Plex
// Mono). One honest rule governs shape: BLOCKY things (square edges, 2px left
// rules, small-caps labels) are live annotations Addison is showing you;
// ROUNDED things (6–12px cards/inputs/banners, 999px pills) are yours to own,
// run, or act on. Amended 2026-07 (v3) — this SUPERSEDES the earlier dark
// terminal-adjacent look (which itself superseded design-doc §7.1's cool-slate).
// Still NOT a generic AI-chat aesthetic (no purple, glassmorphism, sparkle
// icons, shimmer) and deliberately distant from any model vendor's branding (no
// cream/terracotta, no steel blue). The design handoff at docs/design-brief-fern
// is authoritative for tokens, type, shape, and copy.
//
// Colors are driven by CSS custom properties (channels declared in
// src/styles.css: :root = light, .dark = dark) so the whole theme flips with one
// class on <html>. darkMode:"class"; the class is toggled from Settings →
// Appearance and persisted in localStorage ("addison.theme").
const withOpacity = (v) => `rgb(var(${v}) / <alpha-value>)`;

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm surfaces, light → dark via the CSS vars.
        paper: withOpacity("--c-paper"), // app background
        side: withOpacity("--c-side"), // sidebar background
        surface: withOpacity("--c-surface"), // cards, composer
        line: withOpacity("--c-line"), // 1px borders
        hair: withOpacity("--c-hair"), // active-row bg, progress track
        // Text, high → low emphasis.
        ink: withOpacity("--c-ink"), // primary text
        "ink-soft": withOpacity("--c-ink-soft"), // secondary text
        muted: withOpacity("--c-muted"), // tertiary text
        faint: withOpacity("--c-faint"), // labels, placeholders (dim by design)
        // The single accent: fern green. Reserved for Addison's voice, primary
        // actions, and live state — never decoration.
        fern: withOpacity("--c-fern"), // primary buttons, live dots
        "fern-deep": withOpacity("--c-fern-deep"), // accent text/links, hover
        "fern-tint": withOpacity("--c-fern-tint"), // consent card, selected, pills
        // Live-annotation + structural accents.
        rule: withOpacity("--c-rule"), // 2px "Addison's work" left rule
        dash: withOpacity("--c-dash"), // dashed "add widget" border
        pine: withOpacity("--c-pine"), // setup banner bg (high-contrast block)
        // Pine-banner text scale (first-run). Fixed across themes — the pine
        // block never inverts. See :root in styles.css (not overridden in .dark).
        "pine-soft": withOpacity("--c-pine-soft"), // eyebrow small-caps
        "pine-ink": withOpacity("--c-pine-ink"), // serif headline (cream)
        cream: withOpacity("--c-cream"), // body text / step circle / button
        "pine-body": withOpacity("--c-pine-body"), // current-step description
        "pine-muted": withOpacity("--c-pine-muted"), // skip / later-step text
        "pine-line": withOpacity("--c-pine-line"), // outlined step circle
        // Text that sits ON a fern-filled button: white on light, dark on the
        // lightened dark-mode fern. One token, flips with the theme.
        "on-accent": withOpacity("--c-on-accent"),
        // Quiet warm notice (status banners) + errors.
        notice: withOpacity("--c-notice"),
        "notice-tint": withOpacity("--c-notice-tint"),
        danger: withOpacity("--c-danger"),
      },
      fontFamily: {
        // Public Sans is the default body/UI family.
        sans: [
          '"Public Sans"',
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        // Source Serif 4 — the "correspondence" voice (message text, greetings).
        serif: ['"Source Serif 4"', "Georgia", "ui-serif", "serif"],
        // IBM Plex Mono — machine facts only (counts, latency, model tags).
        mono: [
          '"IBM Plex Mono"',
          "ui-monospace",
          '"SF Mono"',
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      fontSize: {
        // Fern type scale (docs/design-brief-fern). Each token is a PLAIN string
        // — never the [size, lineHeight] tuple — because these replaced arbitrary
        // text-[Npx] utilities that set only font-size; a tuple would introduce a
        // line-height where none existed and shift layout. The default Tailwind
        // scale (text-xs/sm/base/…) stays untouched and keeps its built-in
        // line-heights.
        //   tick      10px    smallest mono details (tray caret, connection detail)
        //   label     10.5px  small-caps section/sender labels (with tracking-caps-*)
        //   fact      11px    mono machine facts, pill captions
        //   fine      11.5px  fine print — helper text, consequence lines
        //   hint      12px    dim hints, small controls, mono key inputs
        //   meta      12.5px  meta text — work-list steps, subtitles, text buttons
        //   control   13px    controls, chat items, header titles
        //   action    13.5px  action buttons (Send), item names
        //   row       14px    prominent rows / mobile touch targets
        //   body      15px    Public Sans UI body
        //   message   17px    Source Serif message text (pairs with leading-[1.7])
        //   glyph     19px    icon-glyph characters (mobile ☰)
        //   title     20px    in-window page titles (Settings)
        //   headline  24px    pine-banner serif headline
        //   greeting  32px    serif time-of-day greeting
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
        greeting: "32px",
      },
      letterSpacing: {
        // Fern tracking tokens (plain em strings; defaults tight/wide/… untouched).
        //   logo       -0.02em  "Addison" wordmark tighten
        //   display    -0.01em  serif greeting/headline tighten
        //   emphasis    0.02em  semibold titles, slight opening
        //   caps        0.06em  compact uppercase labels
        //   caps-wide   0.09em  small-caps section labels
        //   caps-wider  0.11em  small-caps eyebrow/sender labels
        logo: "-0.02em",
        display: "-0.01em",
        emphasis: "0.02em",
        caps: "0.06em",
        "caps-wide": "0.09em",
        "caps-wider": "0.11em",
      },
      borderRadius: {
        // 6 small buttons/selects · 8 inputs/rows · 10 cards/composer ·
        // 12 banners · 999 pills.
        sm: "6px",
        DEFAULT: "8px",
        card: "10px",
        banner: "12px",
        pill: "999px",
      },
      boxShadow: {
        // The only two shadows in the app.
        soft: "0 1px 4px rgba(34,38,31,.05)", // composer / cards
        // Setup-banner lift, reworked 2026-07-19 (owner: the handoff's
        // 0 6px 18px/.18 smeared on light paper) — now a tight, quiet edge in
        // the same register as `soft`. Named `banner`, NOT `pine`: a token name
        // shared between `colors` and `boxShadow` makes Tailwind emit
        // `shadow-pine` twice (box-shadow AND shadow-color), and the color
        // variant wins with an OPAQUE shadow — that was the original smear.
        banner: "0 1px 6px rgba(30,43,37,.10)",
      },
    },
  },
  plugins: [],
};
