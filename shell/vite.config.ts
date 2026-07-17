import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the Addison shell frontend (engineering-spec §11 step 7).
// Port 5173 matches tauri.conf.json's devUrl. No external hosts are referenced
// anywhere — the app runs under a strict `default-src 'self'` CSP.
export default defineConfig({
  plugins: [react()],
  // Tauri serves the built assets from ../dist (see tauri.conf.json), which is
  // Vite's default outDir, so no build.outDir override is needed.
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
  },
});
