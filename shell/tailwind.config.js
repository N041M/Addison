/** @type {import('tailwindcss').Config} */
// Addison's visual direction is binding (design-doc §7.1): a calm, green-tinted
// everyday-utility look — NOT a generic AI-chat aesthetic. No purple/indigo, no
// glassmorphism, sharp corners (no rounded cards), one deeper green accent
// reserved for primary actions only. Type is tuned for readability (personas
// are 54 and 68).
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Green-tinted "paper" surfaces.
        paper: "#eff4ee", // app background (light green-tinted off-white)
        surface: "#f9fbf7", // cards / message surfaces
        line: "#d3ddd1", // hairline borders (green-gray)
        // Text — high contrast, green-tinted near-blacks.
        ink: "#1e2a21", // primary body text
        "ink-soft": "#39463c", // slightly softer body text
        muted: "#55655a", // secondary / labels (AA on paper)
        // Single accent: a deep forest green, deliberately not AI-purple.
        accent: "#2f6647", // primary actions
        "accent-dark": "#26543a", // hover / active
        "accent-tint": "#dfeae1", // very soft green wash for selected states
        // Quiet notice colors (status banners) — olive, low-key.
        notice: "#5c6b2c",
        "notice-tint": "#edf2dd",
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
      boxShadow: {
        // Soft, low — no glow, no glassmorphism.
        card: "0 1px 2px rgba(30, 42, 33, 0.06)",
        drawer: "-8px 0 24px rgba(30, 42, 33, 0.10)",
      },
    },
  },
  plugins: [],
};
