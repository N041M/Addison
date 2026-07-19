// ESLint flat config for the Addison shell (React + TS frontend).
//
// Deliberately fast: typescript-eslint's *non-type-aware* recommended set (no
// `project` service), so `eslint src` stays a quick syntactic lint, not a second
// type-check — `tsc --noEmit` is the type authority. Scope is `src` only; the
// build output (dist/), the Rust side (src-tauri/), and node_modules are ignored.

import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";

export default tseslint.config(
  { ignores: ["dist/", "src-tauri/", "node_modules/"] },
  // A disable comment that no longer suppresses anything is itself a finding —
  // stale disables hide real regressions behind "already handled".
  { linterOptions: { reportUnusedDisableDirectives: "error" } },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    languageOptions: {
      globals: { ...globals.browser },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Rules-of-hooks violations are always bugs — keep them fatal.
      "react-hooks/rules-of-hooks": "error",
      // Every finding was reviewed; real/harmless ones are fixed, the handful of
      // deliberate patterns (lazy wrappers, mount-only effects, refs mirroring
      // state) carry a targeted per-line disable with a reason. So: error.
      "react-hooks/exhaustive-deps": "error",
    },
  },
  {
    // parse.ts declares a dependency-free contract in its header: it sits below
    // the low-level modules that import it, so it must never import from anything
    // that imports it. Enforce it mechanically — no *runtime* relative import may
    // originate here. `allowTypeImports` keeps the existing `import type` (erased
    // at build) legal, matching the file's stated stance.
    files: ["src/lib/parse.ts"],
    rules: {
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["./*", "../*"],
              allowTypeImports: true,
              message:
                "lib/parse.ts must stay runtime dependency-free (see its header). Use `import type` only.",
            },
          ],
        },
      ],
    },
  },
  {
    // Test files: linted (per the task) but not type-checked by tsc (tsconfig
    // excludes them). Give them the test/node globals so describe/it/vi resolve.
    files: ["src/**/*.test.{ts,tsx}", "src/__tests__/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
);
