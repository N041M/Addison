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
//     grammar upstream, so this module declares one of its own below rather than
//     admitting `vs/language/json`, see `JSON_TOKENIZER`.)
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

// JSON, the one common file type this build had no colour for.
//
// A GRAMMAR OF OUR OWN, and NOT `monaco-editor/esm/vs/language/json`. That package is
// one of the four language SERVICES point 1 above excludes on purpose: it brings a web
// worker, a schema store, validation and completions. That is machinery for EDITING,
// on a screen that is read-only, and a second worker to answer for in a CSP that names
// `worker-src 'self'` as a decision. What was actually missing is thirty lines of
// tokenizer, so that is what this is: a Monarch grammar on the main thread, the same
// kind of thing every `basic-languages` entry ships, registered through the same
// registry `languageForPath` asks. Nothing about the "no language services" property
// changes.
//
// THE TOKEN NAMES ARE THE SKIN. `monacoTheme.ts` maps by Monaco's token-prefix
// matching, so these are chosen from the names that file already colours rather than
// invented: `key` (an object's field name, `--hl-attr`), `string`, `number`, `keyword`
// (`true`/`false`/`null`), `delimiter` and `comment`. A name outside that map would
// fall to `editor.foreground` under `inherit: false`, no crash, just an invisible
// rule, which is exactly why the map is the thing to write against.
const JSON_LANGUAGE_ID = "json";

const JSON_TOKENIZER: monaco.languages.IMonarchLanguage = {
  defaultToken: "",
  tokenizer: {
    root: [
      // A field NAME is a string followed by a colon, and it is worth its own token
      // for the reason `--hl-attr` exists: the shape of a JSON file is its keys, and
      // colouring them like their values leaves a wall of one hue.
      [/"(?:[^"\\]|\\.)*"(?=\s*:)/, "key"],
      [/"(?:[^"\\]|\\.)*"/, "string"],
      [/-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?/, "number"],
      [/\b(?:true|false|null)\b/, "keyword"],
      [/[{}[\],:]/, "delimiter"],
      // Comments are not JSON and are ordinary in the files people actually open with
      // this name (`tsconfig.json`, `.vscode/*.json`). A viewer that painted them as
      // errors would be telling somebody their working file is broken.
      [/\/\/.*$/, "comment"],
      [/\/\*/, "comment", "@comment"],
    ],
    comment: [
      [/[^/*]+/, "comment"],
      [/\*\//, "comment", "@pop"],
      [/[/*]/, "comment"],
    ],
  },
};

monaco.languages.register({
  id: JSON_LANGUAGE_ID,
  // `.jsonc` alongside `.json` because the tokenizer above treats them the same, and
  // `.webmanifest` because it is JSON under a name that hides it.
  extensions: [".json", ".jsonc", ".webmanifest"],
  aliases: ["JSON", "json"],
  mimetypes: ["application/json"],
});
monaco.languages.setMonarchTokensProvider(JSON_LANGUAGE_ID, JSON_TOKENIZER);
monaco.languages.setLanguageConfiguration(JSON_LANGUAGE_ID, {
  // Brackets only. Everything else a language configuration can carry (auto-closing
  // pairs, surrounding pairs, indentation rules) is for typing, and nothing on this
  // screen types. What brackets buy a reader is the matching highlight when the cursor
  // sits on one, in a format whose whole structure is brackets.
  brackets: [
    ["{", "}"],
    ["[", "]"],
  ],
  comments: { lineComment: "//", blockComment: ["/*", "*/"] },
});

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
