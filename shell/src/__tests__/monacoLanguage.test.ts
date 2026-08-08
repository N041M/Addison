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

import { describe, it, expect } from "vitest";
import { languageForPath } from "../lib/monaco";

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
