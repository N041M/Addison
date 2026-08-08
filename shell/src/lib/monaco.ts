// The Monaco editor, loaded exactly once and configured exactly once.
//
// THIS MODULE IS NEVER IMPORTED STATICALLY. `CodeEditor.tsx` reaches it with a
// dynamic `import("../lib/monaco")` inside an effect, mirroring
// `MermaidDiagram.tsx`'s precedent, so Vite code-splits the whole editor into its
// own lazy chunk and a person who never opens the code screen never downloads it.
// Every test that renders that screen mocks THIS path — Monaco cannot run in jsdom
// (it needs real layout, `ResizeObserver` and `matchMedia`).
//
// FOUR IMPORT DECISIONS, each of which is a constraint rather than a preference:
//
//  1. `monaco-editor/editor/editor.api` — the API entry, NOT the bare
//     `monaco-editor` package. The bare package drags in all four language
//     SERVICES (typescript, json, css, html), each with its own web worker and its
//     own completion/diagnostics machinery. This screen is a read-only viewer: it
//     offers no completions and no diagnostics, and importing the api entry makes
//     that structurally true rather than a config claim somebody can undo.
//  2. NOT `@monaco-editor/react`. It loads Monaco from jsDelivr by default — an
//     instant CSP violation and a network dependency in a local-first app.
//  3. The basic-language REGISTRATIONS, and only those. Each `register.js` is a
//     few hundred bytes declaring an id, its file extensions and a `loader()`; the
//     grammar itself is a dynamic import, so a language's tokenizer chunk is
//     fetched the first time a file of that type is opened and never otherwise.
//     These are Monarch tokenizers running on the main thread — they are what makes
//     the palette in `monacoTheme.ts` visible at all, and they are not the language
//     services point 1 excludes. (JSON is the one common type with no Monarch
//     grammar upstream; a `.json` file renders as plain text rather than dragging
//     in the JSON language service and its worker for colour.)
//  4. The editor worker via Vite's `?worker` suffix — NEVER `?worker&inline`,
//     which produces a `blob:` URL, and never `MonacoEnvironment.getWorkerUrl`,
//     which is the AMD recipe and is where every "Monaco needs blob:" answer
//     online comes from. `worker-src 'self'` in the CSP refuses both, deliberately.
//     The worker is what computes the diff, so this is not optional decoration.

import * as monaco from "monaco-editor/editor/editor.api";
import "monaco-editor/basic-languages/monaco.contribution";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";

// One worker for everything. There are no language workers to switch on: the only
// worker this build contains is the editor's own, which is what computes diffs and
// word ranges. A `label` switch here would be a lie about what is available.
self.MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};

/**
 * The language id for a path, or `undefined` for "plain text".
 *
 * Asked against Monaco's own registry rather than a table of our own, so the set
 * of languages the viewer highlights is exactly the set the editor knows about —
 * one source of truth, and adding a language is an import rather than two edits.
 * Matching is by exact filename first (`Dockerfile`, `Makefile`) and then by the
 * longest extension that matches, so `.d.ts` beats `.ts` when both are declared.
 */
export function languageForPath(path: string): string | undefined {
  const name = path.split(/[\\/]/).pop() ?? "";
  if (!name) return undefined;
  const lower = name.toLowerCase();
  let best: { id: string; length: number } | undefined;
  for (const language of monaco.languages.getLanguages()) {
    for (const filename of language.filenames ?? []) {
      if (filename.toLowerCase() === lower) return language.id;
    }
    for (const extension of language.extensions ?? []) {
      const ext = extension.toLowerCase();
      if (lower.endsWith(ext) && (!best || ext.length > best.length)) {
        best = { id: language.id, length: ext.length };
      }
    }
  }
  return best?.id;
}

export type MonacoApi = typeof monaco;

export default monaco;
