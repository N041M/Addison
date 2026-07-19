import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Minimal Vitest rig for the shell frontend (maintainability review 2026-07-19,
// items 4+5). No Tauri harness, no e2e, no browser — jsdom only, for the
// defensive parsers and the useTurn race guard. Mirrors the app build's plugin
// (React) and bundler-style resolution; TS settings come from tsconfig.json
// (test files are excluded there so `tsc`/`npm run build` never compile them —
// Vitest transforms them itself via esbuild).
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: false,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
