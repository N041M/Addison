/// <reference types="vite/client" />

// Vite's ambient module declarations, pulled in explicitly.
//
// `tsconfig.json` sets no `types` array, and `tsconfig.test.json` pins it to
// `["node"]`, so nothing was loading Vite's client types — which is where the
// `?worker` import suffix is declared. Monaco's editor worker is bundled with that
// suffix (never `?worker&inline`, which produces a `blob:` URL the CSP forbids), so
// the declaration has to be reachable from BOTH configs. A triple-slash reference
// inside `src/` is: it is honoured regardless of the `types` field.
