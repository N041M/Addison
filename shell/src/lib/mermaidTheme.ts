// Mermaid, skinned to the dark direction — one palette, zero new tokens.
// (Same approach as `monacoTheme.ts`: a library that arrives with somebody else's
// theme attached is re-pointed at `styles.css` rather than given a palette of its
// own.)
//
// WHY A DIAGRAM NEEDS TWO HALVES, and why that is not two palettes. Mermaid ships
// its colours as a `<style>` block INSIDE the SVG it returns — and
// `lib/sanitizeSvg.ts` removes every stylesheet from that markup before it is
// injected, because a `<style>` element applies to the whole document and a
// model-authored `classDef` could therefore select the consent card. So the
// colours mermaid computes from `themeVariables` never reach the screen, and the
// paint that does is the first-party rule set in `src/styles.css`
// (`.mermaid-diagram …`). Both read the SAME `--c-*` variables, so there is one
// palette with two consumers, not two palettes:
//
//   * THIS FILE feeds mermaid the values it bakes into GEOMETRY and into the few
//     attributes it writes directly — above all `fontFamily`/`fontSize`, which is
//     what mermaid MEASURES text with when it decides how wide a node must be.
//   * `styles.css` paints, using `var(--c-…)` directly, so a theme flip recolours
//     an already-drawn diagram the moment the `dark` class moves.
//
// THE FONT IS THE CLIPPING BUG (KNOWN-BUGS #15). Mermaid measured labels in its
// default `"trebuchet ms", verdana, arial` at 16px, sized every node from that,
// and the app then painted them in 'Helvetica Neue' at the chat's own size — so
// "Task request" was laid out for one font and drawn in another, and the wider
// drawing was cut off by the box that had been sized for the narrower one. The two
// constants below are the app's real UI stack and are used BY BOTH halves: change
// one and change the matching rule in `styles.css`.
//
// NO ACCENT. The violet is reserved for actions, selection and live state, and a
// diagram has none of the three in it — a node fill would be decoration, which is
// the one thing the accent is never for (CLAUDE.md, design-brief-dark). Nodes take
// `panel` on `paper` with a `rail` hairline, edges take `muted`. That is also why
// this file reads no `--c-accent`.

import type { MermaidConfig } from "mermaid";
import type { ResolvedTheme } from "./theme";

export type { ResolvedTheme };

/** The app's UI stack, byte-for-byte (tailwind.config.js / styles.css). System
 * stacks only — there is no @font-face anywhere and this must not introduce one.
 * `styles.css` sets the identical family on `.mermaid-diagram svg`; they are one
 * decision in two places because mermaid needs it as a config value and the SVG
 * needs it as CSS. */
export const DIAGRAM_FONT_FAMILY = '"Helvetica Neue", Helvetica, Arial, sans-serif';
/** A step DOWN from the chat body (15.5px): a diagram is a figure beside the
 * prose, not more prose. Must equal the `font-size` on `.mermaid-diagram svg`. */
export const DIAGRAM_FONT_SIZE = 14;

// The only hex in this file, and only for the case where no variable can be read
// at all (jsdom; a stylesheet that failed to load). `--c-ink` and `--c-paper` in
// each theme: everything then renders as plain foreground on plain background,
// which is the right way to be wrong. Mermaid hands colours to khroma, which
// THROWS on an unparseable one — a missing fallback is a crash, not a wrong hue.
const FALLBACK: Record<ResolvedTheme, { ink: string; paper: string }> = {
  light: { ink: "#1B1B1D", paper: "#F7F7F5" },
  dark: { ink: "#E9E9E7", paper: "#0C0C0D" },
};

function readChannels(variable: string, root: HTMLElement): [number, number, number] | null {
  let raw = "";
  try {
    raw = getComputedStyle(root).getPropertyValue(variable);
  } catch {
    // getComputedStyle can throw on a detached element in some engines.
    return null;
  }
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

/**
 * One CSS custom property as `#RRGGBB`, falling back to ink or paper.
 *
 * The variables hold SPACE-SEPARATED RGB CHANNELS (`110 112 118`) — that shape is
 * load-bearing elsewhere (Tailwind's `rgb(var(--c-x) / <alpha-value>)` mapping), so
 * the conversion happens here rather than by adding hex duplicates to the tokens.
 */
function readColor(
  variable: string,
  theme: ResolvedTheme,
  fallback: "ink" | "paper",
  root: HTMLElement,
): string {
  const channels = readChannels(variable, root);
  return channels ? toHex(channels) : FALLBACK[theme][fallback];
}

/**
 * The whole mermaid configuration for `theme`, read off the live palette.
 *
 * Pure and exported so it can be asserted without rendering a diagram: mermaid
 * cannot render under jsdom at all (it needs `getBBox`), so a skin that could only
 * be checked by looking at a picture would be a skin nobody checks.
 */
export function buildMermaidConfig(
  theme: ResolvedTheme,
  root: HTMLElement = document.documentElement,
): MermaidConfig {
  const color = (variable: string, fallback: "ink" | "paper" = "ink") =>
    readColor(variable, theme, fallback, root);

  const paper = color("--c-paper", "paper");
  const panel = color("--c-panel", "paper");
  const ink = color("--c-ink");
  const inkSoft = color("--c-ink-soft");
  const muted = color("--c-muted");
  const line = color("--c-line", "paper");
  const rail = color("--c-rail", "paper");
  const danger = color("--c-danger");

  return {
    startOnLoad: false,
    // Unchanged, and not this file's to change: diagrams are display-only.
    securityLevel: "strict",
    // "base" is mermaid's own name for "take the variables below and derive the
    // rest from them" — the named themes ("neutral"/"dark") would each bring a
    // palette this app does not own.
    theme: "base",
    darkMode: theme === "dark",
    fontFamily: DIAGRAM_FONT_FAMILY,
    themeVariables: {
      fontFamily: DIAGRAM_FONT_FAMILY,
      fontSize: `${DIAGRAM_FONT_SIZE}px`,
      background: paper,
      // Node fill / border / label. `panel` on `paper` with a `rail` hairline is
      // the app's own quiet-surface pairing; `primaryColor` is mermaid's name for
      // the default node fill, NOT for an accent.
      primaryColor: panel,
      primaryBorderColor: rail,
      primaryTextColor: ink,
      mainBkg: panel,
      nodeBorder: rail,
      nodeTextColor: ink,
      textColor: ink,
      titleColor: inkSoft,
      // Secondary/tertiary are what mermaid reaches for on alternating bands
      // (clusters, sequence groups). Paper, hairline — the same as a subgraph.
      secondaryColor: paper,
      secondaryBorderColor: line,
      secondaryTextColor: ink,
      tertiaryColor: paper,
      tertiaryBorderColor: line,
      tertiaryTextColor: ink,
      clusterBkg: paper,
      clusterBorder: line,
      // Edges and arrowheads: structure, so tertiary text weight rather than ink.
      lineColor: muted,
      arrowheadColor: muted,
      edgeLabelBackground: paper,
      // Notes are a quiet surface too — never a yellow sticky, which is the one
      // place mermaid's defaults would put a colour this app does not have.
      noteBkgColor: panel,
      noteBorderColor: rail,
      noteTextColor: ink,
      // The only non-neutral: mermaid's parse-error box. Danger is a real state.
      errorBkgColor: paper,
      errorTextColor: danger,
    },
    flowchart: {
      // SVG `<text>`, not an HTML label in a `<foreignObject>`. A foreignObject
      // CLIPS whatever overflows its box, which is how a label that measured
      // narrower than it drew came out as "Task reques"; a `<text>` overflows
      // visibly instead of being cut, so the font half above is belt and this is
      // braces. Kept together on purpose.
      htmlLabels: false,
      // Natural size, so the diagram is drawn at the same type size it was
      // measured at. `.mermaid-diagram` scrolls horizontally when that is wider
      // than the chat column — a diagram scaled down to fit is a diagram nobody
      // over 50 can read, and this app's readers are 54 and 68.
      useMaxWidth: false,
    },
    sequence: { useMaxWidth: false },
    class: { useMaxWidth: false },
    state: { useMaxWidth: false },
    er: { useMaxWidth: false },
    gantt: { useMaxWidth: false },
  };
}
