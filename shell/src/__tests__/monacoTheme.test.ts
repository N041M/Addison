// The Monaco skin (Phase-3 review-surface plan §5): one palette, zero new tokens.
//
// THE TRAP THIS FILE EXISTS TO DEFEAT. jsdom parses no stylesheets, so
// `getComputedStyle(el).getPropertyValue("--hl-keyword")` answers `""` for every
// custom property in the app — which means a theme test that merely renders and
// compares would compare two objects full of the same fallback and pass while the
// converter read nothing at all. Every test below therefore sets the `--hl-*` and
// `--c-*` variables EXPLICITLY on an element (an inline `style.setProperty` is the
// one thing jsdom does answer) and asserts against the values it put there. The
// fallback path gets its own test, where it is the subject rather than an accident.
//
// THE VALUES ARE READ OUT OF `src/styles.css` AT TEST TIME, which is what makes the
// sentence above true. They used to be a hand transcription of that file sitting in
// two dictionaries here — so the token file and this converter could diverge in
// silence, the one thing this file claims to prevent: changing `--hl-keyword` to
// `0 255 0` and dark `--c-danger` to `10 20 30` left the whole suite green
// (2026-08-08). Parsing the real file also lets the "one accent plus danger" rules
// below be asserted BETWEEN TOKENS as they actually stand, rather than between two
// numbers a test wrote out equal by hand and then compared.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  CODE_FONT_FAMILY,
  CODE_FONT_SIZE,
  CODE_LINE_HEIGHT,
  MONACO_THEME_NAMES,
  applyMonacoTheme,
  buildMonacoTheme,
} from "../lib/monacoTheme";

const STYLES = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");

/**
 * Every `R G B` custom property declared under one selector, merged across that
 * selector's blocks in source order.
 *
 * `styles.css` declares the surface colours in one `:root` and the highlight
 * palette in another (and the same for `.dark`), so both blocks have to be read —
 * and a later declaration wins, exactly as the cascade says. Only channel triples
 * are taken: the shadows and font stacks alongside them are not colours this
 * converter ever reads.
 */
function tokensFor(selector: ":root" | ".dark"): Record<string, string> {
  const out: Record<string, string> = {};
  const blocks = new RegExp(`^${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`, "gm");
  for (const block of STYLES.matchAll(blocks)) {
    for (const decl of block[1].matchAll(/(--[\w-]+):\s*(\d{1,3} \d{1,3} \d{1,3})\s*;/g)) {
      out[decl[1]] = decl[2];
    }
  }
  return out;
}

const LIGHT = tokensFor(":root");
const DARK = tokensFor(".dark");

/** `109 91 208` → `#6D5BD0`. Deliberately NOT the converter under test — see the
 * test that anchors it on a value checkable by hand. */
function hexOf(channels: string): string {
  return (
    "#" +
    channels
      .split(" ")
      .map((n) => Number(n).toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()
  );
}

/** Every variable `monacoTheme.ts` reads. A parse that quietly found nothing would
 * make every assertion below vacuous, so the set is checked before anything else. */
const READ_BY_THE_CONVERTER = [
  "--hl-comment",
  "--hl-keyword",
  "--hl-string",
  "--hl-number",
  "--hl-title",
  "--hl-attr",
  "--hl-type",
  "--hl-name",
  "--hl-addition",
  "--hl-deletion",
  "--c-panel",
  "--c-line",
  "--c-rail",
  "--c-ink",
  "--c-muted",
  "--c-faint",
  "--c-ghost",
  "--c-accent",
  "--c-danger",
];

describe("the token file this whole skin reads", () => {
  it("parses, and holds every variable the converter asks for", () => {
    // Kills: a regex that matches nothing (or matches only the first `:root`), which
    // would turn every assertion in this file into a comparison of two empty
    // strings.
    for (const variable of READ_BY_THE_CONVERTER) {
      expect(LIGHT[variable], `light ${variable} missing from styles.css`).toMatch(
        /^\d{1,3} \d{1,3} \d{1,3}$/,
      );
      expect(DARK[variable], `dark ${variable} missing from styles.css`).toMatch(
        /^\d{1,3} \d{1,3} \d{1,3}$/,
      );
    }
  });

  it("converts channels to hex the way the app does", () => {
    // The helper's own anchor, on a value checkable by hand and independent of the
    // file: 109 91 208 → #6D5BD0. Without it, a test comparing the converter against
    // `hexOf` would pass while both were wrong in the same way.
    expect(hexOf("109 91 208")).toBe("#6D5BD0");
    expect(hexOf("255 255 255")).toBe("#FFFFFF");
  });

  it("keeps the code palette on the accent, so no third hue enters", () => {
    // The direction's rule, asserted between the tokens AS THEY STAND rather than
    // against a number written here. `--hl-keyword` and `--hl-link` are the accent
    // itself in both themes; a keyword that stopped being the accent is a second
    // brand colour on the busiest screen in the app. Kills: a palette edit that
    // drifts the editor away from the direction.
    expect(LIGHT["--hl-keyword"]).toBe(LIGHT["--c-accent"]);
    expect(DARK["--hl-keyword"]).toBe(DARK["--c-accent"]);
    expect(LIGHT["--hl-link"]).toBe(LIGHT["--c-accent"]);
    expect(DARK["--hl-link"]).toBe(DARK["--c-accent"]);
  });
});

function paint(vars: Record<string, string>): HTMLElement {
  const root = document.documentElement;
  for (const [name, value] of Object.entries(vars)) root.style.setProperty(name, value);
  return root;
}

function clear(): void {
  const root = document.documentElement;
  for (const name of [...Object.keys(LIGHT), ...Object.keys(DARK)]) {
    root.style.removeProperty(name);
  }
}

beforeEach(clear);
afterEach(clear);

function rule(theme: ReturnType<typeof buildMonacoTheme>, token: string) {
  return theme.rules.find((r) => r.token === token);
}

describe("the palette actually comes from the --hl-* variables", () => {
  it("converts space-separated RGB channels to the hex Monaco takes", () => {
    // Against the values in `styles.css`, not against a transcription of them: a
    // token edit that the converter did not follow fails here. Kills: reading the
    // wrong variable for a token, and kills a converter that stops reading at all
    // (every rule would fall to the ink fallback, which is not what these are).
    const theme = buildMonacoTheme("light", paint(LIGHT));
    expect(rule(theme, "keyword")?.foreground).toBe(hexOf(LIGHT["--hl-keyword"]));
    expect(rule(theme, "string")?.foreground).toBe(hexOf(LIGHT["--hl-string"]));
    expect(rule(theme, "comment")?.foreground).toBe(hexOf(LIGHT["--hl-comment"]));
    expect(theme.colors["editor.background"]).toBe(hexOf(LIGHT["--c-panel"]));
    expect(theme.colors["editor.foreground"]).toBe(hexOf(LIGHT["--c-ink"]));

    const dark = buildMonacoTheme("dark", paint(DARK));
    expect(rule(dark, "keyword")?.foreground).toBe(hexOf(DARK["--hl-keyword"]));
    expect(rule(dark, "invalid")?.foreground).toBe(hexOf(DARK["--c-danger"]));
    expect(dark.colors["editor.background"]).toBe(hexOf(DARK["--c-panel"]));
  });

  it("gives dark and light genuinely different values", () => {
    // Kills: the converter silently reading nothing and both themes collapsing
    // onto the same fallback — which is what "the theme flips" would look like to
    // a test that only checked the two objects were built.
    const light = buildMonacoTheme("light", paint(LIGHT));
    const dark = buildMonacoTheme("dark", paint(DARK));
    expect(dark).not.toEqual(light);
    for (const token of ["comment", "keyword", "string", "number", "type"]) {
      expect(rule(dark, token)?.foreground).not.toBe(rule(light, token)?.foreground);
    }
    expect(dark.colors["editor.background"]).not.toBe(light.colors["editor.background"]);
    expect(dark.base).toBe("vs-dark");
    expect(light.base).toBe("vs");
  });

  it("never inherits from the VS defaults", () => {
    // Kills: `inherit: true`, which lets Visual Studio's own token colours through
    // for everything unmapped — the confetti this palette exists to prevent. The
    // price is that `editor.foreground` must be set, so that is asserted with it.
    for (const theme of [buildMonacoTheme("light", paint(LIGHT)), buildMonacoTheme("dark", paint(DARK))]) {
      expect(theme.inherit).toBe(false);
      expect(theme.colors["editor.foreground"]).toMatch(/^#[0-9A-F]{6}$/);
    }
  });
});

describe("the direction's rules, as rules", () => {
  it("carries exactly one italic and no bold at all", () => {
    // Kills: a `fontStyle: "bold"` creeping in. The app is on system stacks, so a
    // bold token would resolve to a real face — but this direction's mono is a
    // machine voice at one weight, and emphasis lives in hue.
    const theme = buildMonacoTheme("dark", paint(DARK));
    const styled = theme.rules.filter((r) => r.fontStyle);
    expect(styled.map((r) => r.token)).toEqual(["comment"]);
    expect(styled[0].fontStyle).toBe("italic");
    expect(theme.rules.some((r) => (r.fontStyle ?? "").includes("bold"))).toBe(false);
  });

  it("puts no box around the current line", () => {
    // Kills: adding `editor.lineHighlightBorder`, which is how Monaco draws a
    // rectangle round the caret's line — chrome this direction does not own. The
    // line highlight is the hairline value, whatever that value currently is.
    const theme = buildMonacoTheme("dark", paint(DARK));
    expect(theme.colors["editor.lineHighlightBorder"]).toBeUndefined();
    expect(theme.colors["editor.lineHighlightBackground"]).toBe(hexOf(DARK["--c-line"]));
  });

  it("tints the selection with the accent instead of filling it", () => {
    // The single place in the app where the 2px accent rail cannot be used: an
    // editor has no row to hang one on. Kills: an opaque selection fill, and kills
    // tinting it with anything but the accent the token file currently holds.
    const theme = buildMonacoTheme("dark", paint(DARK));
    const selection = theme.colors["editor.selectionBackground"];
    expect(selection.startsWith(hexOf(DARK["--c-accent"]))).toBe(true);
    expect(selection).toHaveLength(9); // #RRGGBBAA — an alpha is present
    expect(selection.slice(7)).not.toBe("FF");
  });

  it("derives the diff ladder from the two existing foregrounds, dark heavier", () => {
    // Kills: inventing background tokens for the diff (the second palette this
    // whole approach exists to avoid), and kills the ladder collapsing so the
    // changed CHARACTERS are no more visible than the changed line.
    const light = buildMonacoTheme("light", paint(LIGHT));
    const dark = buildMonacoTheme("dark", paint(DARK));
    for (const theme of [light, dark]) {
      expect(theme.colors["diffEditor.insertedLineBackground"].slice(0, 7)).toBe(
        theme.colors["diffEditor.insertedTextBackground"].slice(0, 7),
      );
      const line = parseInt(theme.colors["diffEditor.insertedLineBackground"].slice(7), 16);
      const text = parseInt(theme.colors["diffEditor.insertedTextBackground"].slice(7), 16);
      const overview = parseInt(
        theme.colors["diffEditorOverview.insertedForeground"].slice(7),
        16,
      );
      expect(line).toBeLessThan(text);
      expect(text).toBeLessThan(overview);
    }
    // Dark needs more alpha for the same apparent weight against near-black paper.
    expect(parseInt(dark.colors["diffEditor.insertedLineBackground"].slice(7), 16)).toBeGreaterThan(
      parseInt(light.colors["diffEditor.insertedLineBackground"].slice(7), 16),
    );
  });

  it("keeps the deletion tint on the danger colour, so no third hue enters", () => {
    // `--hl-deletion` is byte-identical to `--c-danger` in both themes, and THAT is
    // the claim — asserted between the two tokens as `styles.css` holds them. The
    // version of this test that painted both from dictionaries written equal by hand
    // and then compared them could not fail; changing dark `--c-danger` alone left
    // it green (2026-08-08). Kills: a third hue entering through either token.
    expect(LIGHT["--hl-deletion"]).toBe(LIGHT["--c-danger"]);
    expect(DARK["--hl-deletion"]).toBe(DARK["--c-danger"]);
    for (const [theme, vars] of [
      ["light", LIGHT],
      ["dark", DARK],
    ] as const) {
      const built = buildMonacoTheme(theme, paint(vars));
      expect(built.colors["diffEditor.removedLineBackground"].slice(0, 7)).toBe(
        rule(built, "invalid")?.foreground,
      );
    }
  });
});

describe("the fallback, as the subject rather than as an accident", () => {
  it("still produces a valid theme when no variable can be read", () => {
    // Kills: dropping the fallbacks. Monaco THROWS on an invalid colour, so an
    // empty `getPropertyValue` without one is not a wrong hue — it is a crash on
    // the screen this file skins, and in every test that renders it.
    clear();
    const theme = buildMonacoTheme("dark", document.documentElement);
    for (const r of theme.rules) expect(r.foreground).toMatch(/^#[0-9A-F]{6}$/);
    for (const value of Object.values(theme.colors)) {
      expect(value).toMatch(/^#[0-9A-F]{6}([0-9A-F]{2})?$/);
    }
  });

  it("does not let a garbled value through as a colour", () => {
    // Kills: passing the raw string on. `rgb(1,2,3)` and a channel out of range
    // are both things a stylesheet edit could produce, and both would reach
    // Monaco as an illegal colour.
    const root = paint(DARK);
    root.style.setProperty("--hl-keyword", "not a colour");
    root.style.setProperty("--hl-string", "300 0 0");
    const theme = buildMonacoTheme("dark", root);
    expect(rule(theme, "keyword")?.foreground).toMatch(/^#[0-9A-F]{6}$/);
    expect(rule(theme, "string")?.foreground).toMatch(/^#[0-9A-F]{6}$/);
  });
});

describe("re-theming on the appearance flip", () => {
  it("REDEFINES both themes and then selects one", () => {
    // Kills: calling only `setTheme`. The hex values are baked at define time, so
    // a person who flips Appearance while reading a file would keep the old
    // palette until they reopened it — the exact failure `MermaidDiagram`'s
    // once-per-session read has, which is tolerable for a drawn diagram and wrong
    // for a document somebody is looking at.
    const defined: { name: string; background: string }[] = [];
    const selected: string[] = [];
    const monaco = {
      editor: {
        defineTheme: (name: string, data: { colors: Record<string, string> }) =>
          defined.push({ name, background: data.colors["editor.background"] }),
        setTheme: (name: string) => selected.push(name),
      },
    };

    applyMonacoTheme(monaco, "dark", paint(DARK));
    expect(defined.map((d) => d.name)).toEqual([
      MONACO_THEME_NAMES.light,
      MONACO_THEME_NAMES.dark,
    ]);
    expect(selected).toEqual([MONACO_THEME_NAMES.dark]);

    applyMonacoTheme(monaco, "light", paint(LIGHT));
    expect(selected).toEqual([MONACO_THEME_NAMES.dark, MONACO_THEME_NAMES.light]);
    // Defined AGAIN, against the palette that is live now — not merely re-selected.
    expect(defined).toHaveLength(4);
    expect(defined[3].background).toBe(hexOf(LIGHT["--c-panel"]));
  });
});

describe("the type scale this screen ships", () => {
  it("is 12/19 on the app's own mono stack, and introduces no font", () => {
    // 12px is a deliberate step UP from the nearest precedent
    // (`.markdown-body pre code` is 11.5px), recorded as a tension rather than
    // settled — an editor zoom control is on the follow-up list. Kills: an
    // invented size token, and kills a bundled face sneaking back in (there is no
    // @font-face anywhere in this app).
    expect(CODE_FONT_SIZE).toBe(12);
    expect(CODE_LINE_HEIGHT).toBe(19);
    expect(CODE_FONT_FAMILY).toBe('ui-monospace, "SF Mono", Menlo, monospace');
  });
});
