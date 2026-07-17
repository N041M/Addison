/** @type {import('tailwindcss').Config} */
// Addison's visual direction is binding (design-doc §7.1): a calm, cool-slate
// everyday-utility look — NOT a generic AI-chat aesthetic, and deliberately
// distant from any model vendor's branding (no warm cream/terracotta, no
// purple/indigo, no glassmorphism). Sharp corners (no rounded cards), one deep
// steel-blue accent reserved for primary actions only. Type is tuned for
// readability (personas are 54 and 68).
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Cool "slate paper" surfaces.
        paper: "#f3f5f7", // app background (cool off-white)
        surface: "#fbfcfd", // cards / message surfaces
        line: "#d5dbe2", // hairline borders (cool gray)
        // Text — high contrast, cool near-blacks.
        ink: "#1c242c", // primary body text
        "ink-soft": "#38434e", // slightly softer body text
        muted: "#576372", // secondary / labels (AA on paper)
        // Single accent: a deep steel blue — trustworthy-utility, not AI-purple.
        accent: "#2d5e8b", // primary actions
        "accent-dark": "#244c72", // hover / active
        "accent-tint": "#e3ecf4", // very soft blue wash for selected states
        // Quiet notice colors (status banners) — restrained dark amber, the
        // conventional "heads-up" hue; everything else stays cool.
        notice: "#7a5b18",
        "notice-tint": "#f3edda",
        danger: "#8f3a44",
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          '"Segoe UI"',
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
      },
      boxShadow: {
        // Soft, low — no glow, no glassmorphism.
        card: "0 1px 2px rgba(28, 36, 44, 0.06)",
        drawer: "-8px 0 24px rgba(28, 36, 44, 0.10)",
      },
    },
  },
  plugins: [],
};
