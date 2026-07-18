/** @type {import('tailwindcss').Config} */
// Addison's visual direction is binding: a dark, terminal-adjacent everyday-utility
// look — minimal chrome, system-monospace accents, compact-but-legible type. This
// supersedes design-doc §7.1's original light "cool-slate" palette (amended
// 2026-07, at the owner's decision); the layout/IA and accessibility rules of that
// section are UNCHANGED — only the surface colours moved to dark. Still NOT a
// generic AI-chat aesthetic (no purple gradients, glassmorphism, sparkle icons,
// shimmer) and deliberately distant from any model vendor's branding. Sharp corners
// (no rounded cards), one restrained steel-blue accent reserved for primary actions
// only, no external fonts (strict CSP — system stacks only). Type is tuned for
// readability on dark for older readers (personas are 54 and 68): calm and legible,
// never hacker-neon. The tokens below are authoritative.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Dark terminal-adjacent surfaces, separated by borders (not shadows).
        paper: "#15171b", // app background
        surface: "#1d2026", // cards / raised panels
        line: "#30353d", // hairline borders
        // Text — light on dark, high contrast.
        ink: "#e8eaed", // primary text
        "ink-soft": "#c6cbd2", // slightly softer body text
        muted: "#8b94a1", // secondary / labels (AA on paper)
        // Single accent: a restrained steel blue, lifted for dark — trustworthy
        // utility, not AI-purple, not neon.
        accent: "#6c9fd4", // primary actions
        "accent-dark": "#8db4de", // hover / brighter variant on dark
        "accent-tint": "#23303e", // selected-state wash
        "accent-fg": "#10151b", // text ON accent-filled buttons (white fails AA here)
        // Quiet notice colors (status banners) — restrained amber on dark.
        notice: "#d4b46a",
        "notice-tint": "#2b2619",
        danger: "#e08791",
      },
      fontFamily: {
        // Body stays the plain system sans stack.
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        // Monospace accents (wordmark, labels, timestamps, meta, code) — system
        // stack only, no external fonts.
        mono: [
          "ui-monospace",
          '"SF Mono"',
          "Menlo",
          "Monaco",
          '"Cascadia Mono"',
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        // Dark UIs separate with borders, not shadows — neutralized to near-nothing.
        card: "none",
        drawer: "none",
      },
    },
  },
  plugins: [],
};
