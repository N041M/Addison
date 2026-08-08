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
// Values used here are the real ones from `src/styles.css`, so a test that passes
// is a test that agrees with the token file.

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  CODE_FONT_FAMILY,
  CODE_FONT_SIZE,
  CODE_LINE_HEIGHT,
  MONACO_THEME_NAMES,
  applyMonacoTheme,
  buildMonacoTheme,
} from "../lib/monacoTheme";

// Straight out of styles.css — `:root` and `.dark`.
const LIGHT: Record<string, string> = {
  "--hl-comment": "110 112 118",
  "--hl-keyword": "109 91 208",
  "--hl-string": "60 106 74",
  "--hl-number": "138 96 40",
  "--hl-title": "45 84 120",
  "--hl-attr": "122 92 30",
  "--hl-type": "90 84 140",
  "--hl-name": "180 84 78",
  "--hl-addition": "42 106 66",
  "--hl-deletion": "180 84 78",
  "--c-panel": "255 255 255",
  "--c-line": "228 228 225",
  "--c-rail": "214 214 210",
  "--c-ink": "27 27 29",
  "--c-muted": "91 93 99",
  "--c-faint": "99 101 107",
  "--c-ghost": "110 112 118",
  "--c-accent": "109 91 208",
  "--c-danger": "180 84 78",
};

const DARK: Record<string, string> = {
  "--hl-comment": "144 147 152",
  "--hl-keyword": "180 169 245",
  "--hl-string": "143 191 159",
  "--hl-number": "210 181 122",
  "--hl-title": "138 178 220",
  "--hl-attr": "208 176 122",
  "--hl-type": "190 180 235",
  "--hl-name": "226 166 166",
  "--hl-addition": "143 191 159",
  "--hl-deletion": "226 166 166",
  "--c-panel": "20 21 24",
  "--c-line": "30 31 34",
  "--c-rail": "46 47 51",
  "--c-ink": "233 233 231",
  "--c-muted": "144 147 152",
  "--c-faint": "110 112 118",
  "--c-ghost": "60 62 66",
  "--c-accent": "180 169 245",
  "--c-danger": "226 166 166",
};

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
    // The whole conversion, on one value whose hex is checkable by hand:
    // 109 91 208 → #6D5BD0, which is `--c-accent` in the light theme.
    const theme = buildMonacoTheme("light", paint(LIGHT));
    expect(rule(theme, "keyword")?.foreground).toBe("#6D5BD0");
    expect(rule(theme, "string")?.foreground).toBe("#3C6A4A");
    expect(theme.colors["editor.background"]).toBe("#FFFFFF");
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
    // rectangle round the caret's line — chrome this direction does not own.
    const theme = buildMonacoTheme("dark", paint(DARK));
    expect(theme.colors["editor.lineHighlightBorder"]).toBeUndefined();
    expect(theme.colors["editor.lineHighlightBackground"]).toBe("#1E1F22");
  });

  it("tints the selection with the accent instead of filling it", () => {
    // The single place in the app where the 2px accent rail cannot be used: an
    // editor has no row to hang one on. Kills: an opaque selection fill.
    const theme = buildMonacoTheme("dark", paint(DARK));
    const selection = theme.colors["editor.selectionBackground"];
    expect(selection.startsWith("#B4A9F5")).toBe(true);
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
    // `--hl-deletion` is byte-identical to `--c-danger` in both themes. If that
    // ever stops being true this fails, which is the point: the "one accent plus
    // danger" rule is what keeps this screen inside the direction.
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
    expect(defined[3].background).toBe("#FFFFFF");
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
