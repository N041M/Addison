// `languageForPath` — which grammar the review surface's panes highlight with.
//
// It had no coverage at all: every test that renders the code screen mocks
// `../lib/monaco` wholesale, and the stub answers `undefined` for everything, so the
// real function was never called by anything (2026-08-08). What it decides is not
// cosmetic — the viewer builds a NEW MODEL per file precisely so the language
// travels with the text, and "a viewer that keeps python highlighting on a markdown
// file is worse than one with no highlighting at all" (CodeEditor.tsx).
//
// THE REAL REGISTRY, NOT A FAKE ONE. The whole point of the function is that it asks
// Monaco rather than carrying a table of our own, so a test against a hand-built
// registry would prove the opposite of the thing worth proving. The api entry
// imports cleanly under vitest — nothing here constructs an editor, which is what
// jsdom cannot do.
//
// WHAT CANNOT BE ASSERTED HERE, said plainly rather than faked: the two ordering
// rules in the function (an exact filename beats an extension; the longest matching
// extension wins) have no discriminating case in the registry this build ships —
// no filename entry also matches an extension, and `.html.liquid` is the only
// multi-dot extension and belongs to the same language as `.liquid`. The tests below
// pin the behaviour that IS observable; a case that separates the rules would have
// to invent a registry, and inventing one is how a test comes to agree with itself.

import { describe, it, expect, vi } from "vitest";

// jsdom has no `CSS.escape`, and Monaco's theme service builds an icon stylesheet with
// it the moment anything touches the editor namespace (`monaco.editor.tokenize`
// below). `vi.hoisted` runs before the imports, which is the only place a global can
// be put in front of a module that reads one at load. Two lines of polyfill, and it
// says exactly what jsdom is missing rather than mocking Monaco away; the point of
// this file is that it asks the REAL registry.
vi.hoisted(() => {
  const shim = globalThis as {
    CSS?: { escape(value: string): string };
    matchMedia?: (query: string) => unknown;
  };
  shim.CSS ??= { escape: (value: string) => value };
  // ...and the OS colour-scheme query the same service subscribes to. Neither shim
  // decides anything this file asserts: tokenizing is text in, token names out, and
  // the theme never touches it. (Which theme is in force is deliberately NOT asserted
  // anywhere in jsdom, `test-hardening-plan.md` §5.)
  shim.matchMedia ??= () => ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
  });
});

import monaco, { languageForPath } from "../lib/monaco";

describe("languageForPath", () => {
  it("names the language for the file types this screen actually opens", () => {
    // Kills: returning the id of the FIRST language whose extension list is
    // non-empty, and kills an off-by-one in the extension comparison — both of which
    // put one language's grammar on another language's text.
    expect(languageForPath("/p/src/app.py")).toBe("python");
    expect(languageForPath("/p/README.md")).toBe("markdown");
    expect(languageForPath("/p/src/main.rs")).toBe("rust");
    expect(languageForPath("/p/src/App.tsx")).toBe("typescript");
    expect(languageForPath("/p/notes.txt")).toBe("plaintext");
    // JSON has no Monarch grammar upstream, so `lib/monaco` declares one. Kills:
    // deleting that registration, every `.json` file in a project (and there is one
    // in nearly every project) goes back to rendering as plain text.
    expect(languageForPath("/p/tsconfig.json")).toBe("json");
    expect(languageForPath("/p/.vscode/settings.jsonc")).toBe("json");
  });

  it("matches a filename that has no extension at all", () => {
    // The `filenames` half of the registry — `Dockerfile`, `Gemfile`, `config`. A
    // function that only looked at extensions would answer "plain text" for every
    // one of them. Kills: dropping the filename pass.
    expect(languageForPath("/p/Dockerfile")).toBe("dockerfile");
    expect(languageForPath("/p/Gemfile")).toBe("ruby");
    // ...and it is the NAME being matched, not a prefix of one.
    expect(languageForPath("/p/Dockerfile.py")).toBe("python");
  });

  it("takes the last segment of a Windows path as well as a POSIX one", () => {
    // The shell targets Windows and a root there is `C:\proj`. Kills: splitting on
    // "/" alone, which leaves the whole path as the "filename" and matches nothing —
    // every file on that platform rendering as plain text.
    expect(languageForPath("C:\\proj\\src\\main.rs")).toBe("rust");
    expect(languageForPath("C:\\proj\\Dockerfile")).toBe("dockerfile");
  });

  it("ignores case, because a file on disk carries whatever case somebody typed", () => {
    // Kills: comparing raw. `.PY` and `README.MD` are ordinary things to find in a
    // real project, and macOS filesystems are case-insensitive by default.
    expect(languageForPath("/p/APP.PY")).toBe("python");
    expect(languageForPath("/p/DOCKERFILE")).toBe("dockerfile");
  });

  it("answers 'plain text' by saying nothing, for anything it does not know", () => {
    // `undefined` is what Monaco takes for plain text. Kills: returning "" (which
    // Monaco treats as a language id and does not have) or inventing "plaintext" for
    // a type this build has no grammar for — the viewer would then claim a language
    // it is not highlighting with.
    expect(languageForPath("/p/no-extension")).toBeUndefined();
    expect(languageForPath("/p/archive.zzz")).toBeUndefined();
    expect(languageForPath("/p/")).toBeUndefined();
    expect(languageForPath("")).toBeUndefined();
  });
});

describe("the JSON grammar this build declares itself", () => {
  /** Every token name the tokenizer puts on one line, in order. */
  function tokensFor(line: string): string[] {
    return monaco.editor
      .tokenize(line, "json")[0]
      .map((token) => token.type)
      .filter((type) => type !== "");
  }

  it("colours a field name apart from its value", () => {
    // The one decision in the grammar that is not mechanical: a key is a string
    // followed by a colon, and it takes `key` (which `monacoTheme.ts` maps to
    // `--hl-attr`) rather than `string`. Kills: dropping the lookahead rule, every
    // key and every value becomes one hue, which is a wall rather than a structure.
    const tokens = tokensFor('{"name": "addison"}');
    expect(tokens[0]).toBe("delimiter.json");
    expect(tokens[1]).toBe("key.json");
    expect(tokens[3]).toBe("string.json");
  });

  it("names numbers, the three keywords and the punctuation", () => {
    // Kills: token names outside the map in `monacoTheme.ts`. Under `inherit: false`
    // an unmapped token falls silently to the editor foreground, so a typo here is
    // invisible in every test that does not name the tokens out loud.
    expect(tokensFor("[1, -2.5e3, true, false, null]")).toEqual([
      "delimiter.json",
      "number.json",
      "delimiter.json",
      "number.json",
      "delimiter.json",
      "keyword.json",
      "delimiter.json",
      "keyword.json",
      "delimiter.json",
      "keyword.json",
      "delimiter.json",
    ]);
  });

  it("treats a comment as a comment, not as broken JSON", () => {
    // `tsconfig.json` and the editor settings files people open are jsonc in
    // everything but name. Kills: removing the comment rules, which leaves the text
    // uncoloured and (with a block comment) the rest of the file mis-tokenized.
    expect(tokensFor("// a note")).toEqual(["comment.json"]);
    expect(tokensFor("/* a note */")).toEqual(["comment.json"]);
    // ...and a block comment carries across lines, which is the half a single-line
    // rule gets wrong: the SECOND line of one must not be read as JSON.
    const lines = monaco.editor.tokenize('/* a note\n"not a key": 1 */\n{}', "json");
    expect(lines[1].map((token) => token.type)).toEqual(["comment.json"]);
    expect(lines[2].map((token) => token.type)).toEqual(["delimiter.json"]);
  });

  it("brings no language service and no second worker with it", () => {
    // THE CONSTRAINT, not a detail: the review surface is read-only and its CSP names
    // `worker-src 'self'` as a decision, so JSON colour had to arrive as a grammar
    // rather than as `vs/language/json`. Kills: swapping this module's registration
    // for that import, `jsonDefaults` appears on the api the moment it is loaded.
    expect((monaco.languages as Record<string, unknown>).json).toBeUndefined();
  });
});
