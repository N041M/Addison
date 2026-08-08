import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite config for the Addison shell frontend (engineering-spec §11 step 7).
// Port 5173 matches tauri.conf.json's devUrl. No external hosts are referenced
// anywhere — the app runs under the strict policy in tauri.conf.json, pinned by
// tests/test_csp_is_pinned.py.
export default defineConfig({
  plugins: [react()],
  // Tauri serves the built assets from ../dist (see tauri.conf.json), which is
  // Vite's default outDir, so no build.outDir override is needed.
  clearScreen: false,
  optimizeDeps: {
    // Monaco's ESM tree is thousands of files. Without pre-bundling, a dev cold
    // start crawls over every one of them the first time the code screen opens.
    //
    // The API ENTRY, never the bare `monaco-editor` package: the bare package
    // drags in every language contribution and all four language services, and
    // that single choice is the biggest size lever this screen has. It is also
    // what makes "no language workers" structurally true rather than a config
    // claim somebody can undo.
    include: ["monaco-editor/esm/vs/editor/editor.api"],
  },
  worker: {
    // The editor worker is imported with Vite's `?worker` suffix, which emits a
    // real asset served from the app's own origin. NEVER `?worker&inline`: that
    // produces a `blob:` URL, and `worker-src 'self'` refuses it — deliberately,
    // because a policy that admits `blob:` workers is a policy that admits
    // arbitrary generated code.
    //
    // `iife` is Vite's default and is stated here as a decision: a module worker
    // would need `new Worker(url, {type: "module"})`, and this app ships against
    // three different webviews (WKWebView, WebView2, WebKitGTK) rather than one
    // browser it can check. The worker has no dynamic imports, so bundling it as a
    // classic script costs nothing and asks nothing of the platform.
    format: "iife",
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
