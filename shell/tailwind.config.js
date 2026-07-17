/** @type {import('tailwindcss').Config} */
// Addison's visual direction is binding (design-doc §7.1): a calm, warm-neutral
// everyday-utility look — NOT a generic AI-chat aesthetic. No purple/indigo, no
// glassmorphism, one accent color (a muted forest green) reserved for primary
// actions only. Type is tuned for readability (personas are 54 and 68).
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Warm-neutral "paper" surfaces.
        paper: "#f5f2ea", // app background (warm off-white)
        surface: "#fffdf8", // cards / message surfaces
        line: "#e4ddd0", // hairline borders
        // Text — high contrast, warm grays.
        ink: "#2a2521", // primary body text
        "ink-soft": "#463f38", // slightly softer body text
        muted: "#6b6259", // secondary / labels (AA on paper)
        // Single accent: a muted forest green, deliberately not AI-purple.
        accent: "#3f6b52", // primary actions
        "accent-dark": "#335844", // hover / active
        "accent-tint": "#e7efe9", // very soft green wash for selected states
        // Quiet notice colors (status banners) — warm ochre, low-key.
        notice: "#8a6a2f",
        "notice-tint": "#f6eeda",
        danger: "#8a3b3b",
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
      borderRadius: {
        card: "10px",
      },
      boxShadow: {
        // Soft, low, warm — no glow, no glassmorphism.
        card: "0 1px 2px rgba(42, 37, 33, 0.06)",
        drawer: "-8px 0 24px rgba(42, 37, 33, 0.10)",
      },
    },
  },
  plugins: [],
};
