// Monaco, skinned to the dark direction — one palette, zero new tokens.
// (Phase-3 review-surface plan §5; docs/design-brief-dark/IMPLEMENTATION.md, "The
// code surface".)
//
// A code editor is the one component that arrives with somebody else's theme
// already attached, and this app has exactly one look. The brief has no vocabulary
// for code — but `src/styles.css` already carries a full highlight.js token theme
// for both themes (`--hl-comment` … `--hl-deletion`, tuned toward the violet accent
// when markdown code blocks were restyled). This file reads THOSE EXACT VARIABLES
// and converts them, so the repo carries one code palette instead of two that drift
// apart: change `--hl-keyword` and the markdown blocks and the viewer move together.
//
// FOUR RULES THIS FILE KEEPS, each of which is a decision rather than a detail:
//
//   1. `inherit: false`. Nothing leaks in from the VS defaults, so an unmapped
//      token falls to `editor.foreground` — code, not confetti. The cost is that
//      `editor.foreground` MUST be set, and it is (`--c-ink`).
//   2. Weight 400 only. The app is on system stacks now, so `fontStyle: "bold"`
//      would resolve to a real face rather than a synthetic one — but this
//      direction's mono is a machine voice at one weight, and the palette carries
//      emphasis in hue alone. Italic appears once, on comments, matching the
//      `.hljs-comment` rule that already exists.
//   3. The diff alpha ladder lives HERE, not in `styles.css`. Putting derived
//      background tints in the token file would create the second palette this
//      whole approach exists to avoid.
//   4. Every read has a fallback. jsdom applies no stylesheets, so
//      `getPropertyValue` returns `""` under vitest — and Monaco THROWS on an
//      invalid color, so a missing fallback is not a wrong colour, it is a crash
//      in a test rig. The two hex literals below are the only ones in this file.
//
// Worth knowing, because it is what keeps "one accent plus danger" true:
// `--hl-deletion` is `180 84 78` light and `226 166 166` dark — byte-identical to
// `--c-danger` in both themes. The deletion tint is literally the danger colour at
// low alpha, so no third hue enters the app for this screen.

import type { editor } from "monaco-editor/editor/editor.api";
import type { ResolvedTheme } from "./theme";

/** Which palette is live. App already computes this every time it applies one —
 * re-exported here so the editor files have one import rather than two. */
export type { ResolvedTheme };

// The one place hex appears. `--c-ink` in each theme, used ONLY when a variable
// cannot be read at all (jsdom; a stylesheet that failed to load). Everything then
// renders as plain foreground text, which is the right way to be wrong.
const FALLBACK_INK: Record<ResolvedTheme, string> = {
  light: "#1B1B1D",
  dark: "#E9E9E7",
};

// --- The diff alpha ladder --------------------------------------------------
// `--hl-addition` / `--hl-deletion` exist as FOREGROUNDS; a diff needs backgrounds,
// so they are derived as an alpha ladder over those same foregrounds. Dark needs
// more alpha than light for the same apparent weight against near-black paper.
const DIFF_LINE_ALPHA: Record<ResolvedTheme, number> = { light: 0.1, dark: 0.16 };
const DIFF_TEXT_ALPHA: Record<ResolvedTheme, number> = { light: 0.22, dark: 0.3 };
const DIFF_OVERVIEW_ALPHA: Record<ResolvedTheme, number> = { light: 0.7, dark: 0.7 };

// Selection is a TINT here, and this is the single place in the app where the 2px
// accent rail cannot be used: an editor has no row to hang one on. Same asymmetry
// as the ladder — the light accent is a saturated violet on white, the dark one is
// pale on near-black.
const SELECTION_ALPHA: Record<ResolvedTheme, number> = { light: 0.18, dark: 0.26 };
// The other occurrences of a selected word, and the selection while the editor is
// not focused: the same colour, quieter, so neither competes with the live one.
const SELECTION_ECHO_ALPHA: Record<ResolvedTheme, number> = { light: 0.09, dark: 0.13 };

/** 12px with a 19px line-height — see the tension recorded in the plan and in
 * IMPLEMENTATION.md. The nearest precedent (`.markdown-body pre code`) is 11.5px,
 * so this is a deliberate step UP for a surface read as a document rather than as
 * an excerpt inside a message, and an editor zoom control is on the follow-up list
 * rather than folded in as though 12px settled it. */
export const CODE_FONT_SIZE = 12;
export const CODE_LINE_HEIGHT = 19;
/** The app's mono stack, byte-for-byte (tailwind.config.js / styles.css). System
 * stacks only — there is no @font-face anywhere and this must not introduce one. */
export const CODE_FONT_FAMILY = 'ui-monospace, "SF Mono", Menlo, monospace';

/** The theme names registered with Monaco. Two, rather than one redefined in
 * place: `setTheme` then always has a name change to act on, so a flip can never
 * depend on `defineTheme`'s re-application behaviour. */
export const MONACO_THEME_NAMES: Record<ResolvedTheme, string> = {
  light: "addison-light",
  dark: "addison-dark",
};

// ---------------------------------------------------------------------------
// Reading the palette
// ---------------------------------------------------------------------------

/**
 * One CSS custom property, as `#RRGGBB`.
 *
 * The variables hold SPACE-SEPARATED RGB CHANNELS (`110 112 118`) — that shape is
 * load-bearing elsewhere: it is what lets Tailwind's `rgb(var(--c-x) /
 * <alpha-value>)` mapping keep the opacity utilities working. Monaco takes hex
 * only, so the conversion happens here rather than by adding hex duplicates to the
 * token file.
 */
function readColor(
  variable: string,
  theme: ResolvedTheme,
  root: HTMLElement = document.documentElement,
): string {
  const channels = readChannels(variable, root);
  return channels ? toHex(channels) : FALLBACK_INK[theme];
}

/** The same variable, composited over nothing at `alpha` — `#RRGGBBAA`. */
function readColorAlpha(
  variable: string,
  alpha: number,
  theme: ResolvedTheme,
  root: HTMLElement = document.documentElement,
): string {
  const channels = readChannels(variable, root);
  const base = channels ? toHex(channels) : FALLBACK_INK[theme];
  return base + alphaHex(alpha);
}

function readChannels(variable: string, root: HTMLElement): [number, number, number] | null {
  let raw = "";
  try {
    raw = getComputedStyle(root).getPropertyValue(variable);
  } catch {
    // getComputedStyle can throw on a detached element in some engines. A theme
    // that renders as plain foreground beats one that takes the screen down.
    return null;
  }
  // jsdom answers "" for every custom property — it parses no stylesheets — and an
  // inline `style.setProperty` is the only thing it does answer, which is exactly
  // what the theme tests set.
  const parts = raw.trim().split(/[\s,]+/).filter(Boolean);
  if (parts.length < 3) return null;
  const nums = parts.slice(0, 3).map((p) => Number(p));
  if (nums.some((n) => !Number.isFinite(n) || n < 0 || n > 255)) return null;
  return [nums[0], nums[1], nums[2]];
}

function toHex(channels: [number, number, number]): string {
  return (
    "#" +
    channels
      .map((n) => Math.round(n).toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase()
  );
}

function alphaHex(alpha: number): string {
  const clamped = Math.min(1, Math.max(0, alpha));
  return Math.round(clamped * 255)
    .toString(16)
    .padStart(2, "0")
    .toUpperCase();
}

// ---------------------------------------------------------------------------
// The theme
// ---------------------------------------------------------------------------

/**
 * The token map, by Monaco's token-PREFIX matching: a rule for `string` also
 * covers `string.escape`, `string.quote` and every other language's refinement of
 * it. Written out as a list rather than derived, so the mapping is readable as the
 * decision it is.
 *
 * `--hl-builtin` and `--hl-link` are deliberately absent: Monaco has no token that
 * corresponds to either, and inventing a mapping for them would put a colour on
 * screen that no rule in `styles.css` puts anywhere else. They stay in the token
 * file for the markdown blocks that do use them.
 */
const TOKEN_MAP: { token: string; variable: string; italic?: boolean }[] = [
  // Italic matches the `.hljs-comment` rule that is already in the tree, so a
  // comment reads the same in a message and in the viewer.
  { token: "comment", variable: "--hl-comment", italic: true },
  { token: "keyword", variable: "--hl-keyword" },
  { token: "string", variable: "--hl-string" },
  { token: "regexp", variable: "--hl-string" },
  { token: "number", variable: "--hl-number" },
  { token: "constant", variable: "--hl-number" },
  { token: "type", variable: "--hl-type" },
  { token: "variable", variable: "--hl-name" },
  { token: "tag", variable: "--hl-name" },
  { token: "attribute.name", variable: "--hl-attr" },
  { token: "key", variable: "--hl-attr" },
  { token: "function", variable: "--hl-title" },
  { token: "predefined", variable: "--hl-title" },
  // Punctuation is structure, not content: it takes the app's tertiary text colour
  // rather than a hue of its own.
  { token: "delimiter", variable: "--c-muted" },
  { token: "operator", variable: "--c-muted" },
  { token: "invalid", variable: "--c-danger" },
];

/**
 * Build the theme for `theme`, reading the live values off `root`.
 *
 * Pure and exported so the theme can be asserted without a Monaco instance —
 * Monaco cannot run in jsdom (it needs real layout, `ResizeObserver` and
 * `matchMedia`), and a skin nobody can test is a skin that quietly rots.
 */
export function buildMonacoTheme(
  theme: ResolvedTheme,
  root: HTMLElement = document.documentElement,
): editor.IStandaloneThemeData {
  const color = (variable: string) => readColor(variable, theme, root);
  const alpha = (variable: string, a: number) => readColorAlpha(variable, a, theme, root);

  return {
    base: theme === "dark" ? "vs-dark" : "vs",
    // See rule 1 in the file header. Everything on screen is a value this file
    // chose, or `editor.foreground`.
    inherit: false,
    rules: TOKEN_MAP.map(({ token, variable, italic }) => ({
      token,
      foreground: color(variable),
      // Weight 400 throughout — `fontStyle` carries italic and nothing else.
      ...(italic ? { fontStyle: "italic" } : {}),
    })),
    colors: {
      // `panel` is what `.markdown-body pre` already uses, so code keeps looking
      // the way code looks in this app.
      "editor.background": color("--c-panel"),
      "editor.foreground": color("--c-ink"),
      "editorLineNumber.foreground": color("--c-faint"),
      "editorLineNumber.activeForeground": color("--c-muted"),
      // The hairline/row-separator value — the nearest thing this direction has to
      // a raised row. NO `editor.lineHighlightBorder`: a box around the caret line
      // is chrome this direction does not own.
      "editor.lineHighlightBackground": color("--c-line"),
      "editor.selectionBackground": alpha("--c-accent", SELECTION_ALPHA[theme]),
      "editor.inactiveSelectionBackground": alpha("--c-accent", SELECTION_ECHO_ALPHA[theme]),
      "editor.selectionHighlightBackground": alpha("--c-accent", SELECTION_ECHO_ALPHA[theme]),
      "editorWhitespace.foreground": color("--c-ghost"),
      "editorCursor.foreground": color("--c-accent"),
      // The gutter and the thin strips either side of the text sit on the same
      // panel, so the editor reads as one block rather than three.
      "editorGutter.background": color("--c-panel"),
      "editorWidget.background": color("--c-panel"),
      "editorWidget.border": color("--c-rail"),
      "scrollbarSlider.background": alpha("--c-rail", 0.6),
      "scrollbarSlider.hoverBackground": alpha("--c-rail", 0.85),
      "scrollbarSlider.activeBackground": color("--c-rail"),

      // --- Diff: the alpha ladder, derived from the two existing foregrounds ---
      "diffEditor.insertedLineBackground": alpha("--hl-addition", DIFF_LINE_ALPHA[theme]),
      "diffEditor.removedLineBackground": alpha("--hl-deletion", DIFF_LINE_ALPHA[theme]),
      "diffEditor.insertedTextBackground": alpha("--hl-addition", DIFF_TEXT_ALPHA[theme]),
      "diffEditor.removedTextBackground": alpha("--hl-deletion", DIFF_TEXT_ALPHA[theme]),
      "diffEditorOverview.insertedForeground": alpha(
        "--hl-addition",
        DIFF_OVERVIEW_ALPHA[theme],
      ),
      "diffEditorOverview.removedForeground": alpha(
        "--hl-deletion",
        DIFF_OVERVIEW_ALPHA[theme],
      ),
      // The hatched fill beside a block that exists on one side only. The hairline
      // value, so it reads as absence rather than as a third state with a colour.
      "diffEditor.diagonalFill": color("--c-line"),
    },
  };
}

/**
 * Define BOTH themes against the live palette and select the one for `theme`.
 *
 * Called again on every appearance flip, and that is the point: the hex values are
 * baked at DEFINE time, so `setTheme` alone would leave a person who flips
 * Appearance looking at the old palette until they reopened the file.
 *
 * Deliberately NOT a `MutationObserver` on the `dark` class: App already computes
 * the resolved theme every time it applies one and hands it to `CodeSurface` as a
 * prop, and a second source of truth for a fact the app knows is how the two come
 * to disagree. `MermaidDiagram.tsx` does observe the class, for the reason stated
 * there — a diagram is created inside model-authored markdown, below every
 * component that could be given the prop.
 */
export function applyMonacoTheme(
  monaco: {
    editor: {
      defineTheme(name: string, data: editor.IStandaloneThemeData): void;
      setTheme(name: string): void;
    };
  },
  theme: ResolvedTheme,
  root: HTMLElement = document.documentElement,
): void {
  // Both, so the pair on screen can never be half-stale — a diff editor and a
  // viewer mounted at different moments share one registry.
  monaco.editor.defineTheme(MONACO_THEME_NAMES.light, buildMonacoTheme("light", root));
  monaco.editor.defineTheme(MONACO_THEME_NAMES.dark, buildMonacoTheme("dark", root));
  monaco.editor.setTheme(MONACO_THEME_NAMES[theme]);
}
