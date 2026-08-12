// The mermaid skin, and the three symptoms it fixes (KNOWN-BUGS #15, 2026-08-11).
//
// WHAT WENT WRONG, because every test here is one of the three. `sanitizeSvg.ts`
// strips every `<style>` block out of mermaid's SVG before injection — necessary,
// and it takes mermaid's ENTIRE palette with it, since that is where mermaid puts
// it. Left with the SVG defaults, diagrams rendered:
//
//   1. black node fills and black text, in both themes;
//   2. labels cut mid-word ("Task reques") — mermaid measured them in its default
//      `trebuchet ms` at 16px, sized each node from that, and the app drew them in
//      'Helvetica Neue', so the drawing overflowed a box laid out for a narrower
//      font and the `<foreignObject>` clipped what stuck out;
//   3. edges as fat filled blobs — `.flowchart-link { fill: none }` went with the
//      stylesheet, and an unfilled curve is a filled shape.
//
// THE FIX HAS TWO HALVES and this file checks both, because neither is visible to
// the other. `lib/mermaidTheme.ts` builds the config mermaid MEASURES with;
// `styles.css` paints, against the same tokens. jsdom parses no stylesheets and
// mermaid cannot render in it at all (it needs `getBBox`), so the CSS half is
// asserted at source level — the same technique `monacoTheme.test.ts` uses for the
// token file, and the only one available short of a screenshot.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor, cleanup } from "@testing-library/react";
import {
  DIAGRAM_FONT_FAMILY,
  DIAGRAM_FONT_SIZE,
  buildMermaidConfig,
} from "../lib/mermaidTheme";

const STYLES = readFileSync(join(process.cwd(), "src", "styles.css"), "utf8");

/** The `R G B` custom properties declared under one selector, merged across that
 * selector's blocks in source order — the real token values, not a transcription.
 * (Same reader as monacoTheme.test.ts, for the same reason: a hand-copied palette
 * lets the tokens and their consumer drift in silence.) */
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

/** jsdom applies no stylesheets, so the variables are set INLINE on a detached
 * element — the one thing `getComputedStyle` answers there. Everything asserted
 * below is therefore a value this test put in, read back out through the
 * converter. */
function paint(tokens: Record<string, string>): HTMLElement {
  const el = document.createElement("div");
  for (const [name, value] of Object.entries(tokens)) el.style.setProperty(name, value);
  return el;
}

type ThemeVars = Record<string, string>;
const varsOf = (theme: "light" | "dark", root: HTMLElement): ThemeVars =>
  (buildMermaidConfig(theme, root).themeVariables ?? {}) as ThemeVars;

afterEach(cleanup);

// ===========================================================================
// 1. The palette mermaid is initialized with — token-derived, both themes
// ===========================================================================

describe("buildMermaidConfig reads the app's tokens", () => {
  it("parses the token file at all", () => {
    // Kills: a regex that matches nothing, which would make every hex comparison
    // below a comparison of two empty strings.
    for (const name of ["--c-paper", "--c-panel", "--c-ink", "--c-muted", "--c-rail"]) {
      expect(LIGHT[name], `light ${name}`).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/);
      expect(DARK[name], `dark ${name}`).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/);
    }
  });

  it.each([
    ["dark", DARK],
    ["light", LIGHT],
  ] as const)("maps node fill, border, text and lines for %s", (theme, tokens) => {
    // Symptom 1. Kills: passing mermaid a named theme ("dark"/"neutral"), or a
    // hard-coded palette, or reading the wrong variable for any of the four
    // surfaces a flowchart is actually made of.
    const vars = varsOf(theme, paint(tokens));
    expect(vars.background).toBe(hexOf(tokens["--c-paper"]));
    expect(vars.mainBkg).toBe(hexOf(tokens["--c-panel"]));
    expect(vars.primaryColor).toBe(hexOf(tokens["--c-panel"]));
    expect(vars.nodeBorder).toBe(hexOf(tokens["--c-rail"]));
    expect(vars.nodeTextColor).toBe(hexOf(tokens["--c-ink"]));
    expect(vars.textColor).toBe(hexOf(tokens["--c-ink"]));
    expect(vars.lineColor).toBe(hexOf(tokens["--c-muted"]));
    expect(vars.arrowheadColor).toBe(hexOf(tokens["--c-muted"]));
    expect(vars.clusterBkg).toBe(hexOf(tokens["--c-paper"]));
    expect(vars.clusterBorder).toBe(hexOf(tokens["--c-line"]));
  });

  it("gives the two themes different values, which is the whole point", () => {
    // Kills: reading the variables off `document.documentElement` regardless of the
    // `root` argument — every assertion above would still pass, from one palette.
    expect(varsOf("dark", paint(DARK)).background).not.toBe(
      varsOf("light", paint(LIGHT)).background,
    );
    expect(buildMermaidConfig("dark", paint(DARK)).darkMode).toBe(true);
    expect(buildMermaidConfig("light", paint(LIGHT)).darkMode).toBe(false);
  });

  it("spends no accent on decoration", () => {
    // The direction's own rule: violet is for actions, selection and live state,
    // and a diagram has none of the three in it. Kills: "brighten the diagram up"
    // by filling nodes or drawing edges with `--c-accent`.
    const accent = hexOf(LIGHT["--c-accent"]);
    const accentDark = hexOf(DARK["--c-accent"]);
    for (const [theme, tokens] of [["light", LIGHT], ["dark", DARK]] as const) {
      const values = Object.values(varsOf(theme, paint(tokens)));
      expect(values).not.toContain(accent);
      expect(values).not.toContain(accentDark);
    }
  });

  it("still hands mermaid a parseable colour when no variable can be read", () => {
    // jsdom answers "" for every custom property, and mermaid passes colours to
    // khroma, which THROWS on an unparseable one — so a missing fallback is a crash
    // in the diagram path, not a wrong hue. Kills: returning "" or "rgb(var(…))".
    const bare = document.createElement("div");
    for (const value of Object.values(varsOf("dark", bare))) {
      if (value.startsWith("#")) expect(value).toMatch(/^#[0-9A-F]{6}$/);
    }
    expect(varsOf("dark", bare).background).toBe("#0C0C0D");
    expect(varsOf("light", bare).background).toBe("#F7F7F5");
  });
});

// ===========================================================================
// 2. The clipping half: one font, measured and drawn
// ===========================================================================

describe("the font mermaid measures with is the font the app draws with", () => {
  it("passes the app's UI stack and size into the config", () => {
    // Symptom 2. Kills: dropping `fontFamily`, which returns mermaid to
    // `trebuchet ms` at 16px and the labels to being cut mid-word.
    const config = buildMermaidConfig("dark", paint(DARK));
    expect(config.fontFamily).toBe(DIAGRAM_FONT_FAMILY);
    const vars = varsOf("dark", paint(DARK));
    expect(vars.fontFamily).toBe(DIAGRAM_FONT_FAMILY);
    expect(vars.fontSize).toBe(`${DIAGRAM_FONT_SIZE}px`);
  });

  it("introduces no font — system stacks only, no @font-face anywhere", () => {
    expect(DIAGRAM_FONT_FAMILY).toBe('"Helvetica Neue", Helvetica, Arial, sans-serif');
    expect(STYLES).not.toMatch(/@font-face\s*\{/);
  });

  it("states the same family and size in the stylesheet that paints", () => {
    // The two halves cannot see each other, and a mismatch between them IS the bug.
    // Kills: changing one constant and not the rule.
    const svgRule = STYLES.match(/\.mermaid-diagram svg \{([^}]*)\}/);
    expect(svgRule, "no `.mermaid-diagram svg` rule in styles.css").toBeTruthy();
    expect(svgRule![1]).toContain(DIAGRAM_FONT_FAMILY);
    expect(svgRule![1]).toContain(`font-size: ${DIAGRAM_FONT_SIZE}px`);
  });

  it("draws labels as SVG text, which cannot be clipped by a box sized for less", () => {
    // The braces to the font's belt: an HTML label lives in a `<foreignObject>`,
    // which clips whatever overflows it. Kills: turning `htmlLabels` back on.
    expect(buildMermaidConfig("light", paint(LIGHT)).flowchart?.htmlLabels).toBe(false);
  });
});

// ===========================================================================
// 3. The paint: edges that are lines, arrowheads that are shapes
// ===========================================================================

describe("the stylesheet restates what the sanitizer strips", () => {
  /** The mermaid section of styles.css, comments removed — the RULES, so that a
   * negative assertion cannot be defeated (or triggered) by prose about them. */
  const start = STYLES.indexOf("/* Mermaid diagrams");
  const end = STYLES.indexOf("/* GFM tables", start);
  const section = STYLES.slice(start, end).replace(/\/\*[\s\S]*?\*\//g, "");

  it("finds the section at all", () => {
    // Kills: a slice that silently matches nothing, making every check below pass
    // against an empty string.
    expect(start).toBeGreaterThan(-1);
    expect(end).toBeGreaterThan(start);
    expect(section).toContain(".mermaid-diagram");
  });

  it("gives edges `fill: none`, the rule whose absence drew them as blobs", () => {
    // Symptom 3. Kills: deleting the edge rule, or writing it without `fill: none`
    // on the reasoning that a stroke colour is enough.
    const edgeRule = section.match(/\.mermaid-diagram \.flowchart-link,[^{]*\{([^}]*)\}/);
    expect(edgeRule, "no `.flowchart-link` rule").toBeTruthy();
    expect(edgeRule![1]).toContain("fill: none");
    expect(edgeRule![1]).toContain("stroke: rgb(var(--c-muted))");
  });

  it("does NOT reach the arrowhead markers with that rule", () => {
    // The markers are defined inside the same `edgePaths` group as the edges, so a
    // broad `.edgePaths path { fill: none }` would take the arrowheads' fill away
    // and leave the arrows invisible — the opposite failure, equally wrong. Kills:
    // broadening the selector.
    expect(section).not.toContain(".edgePaths path");
    const markerRule = section.match(/\.mermaid-diagram marker path,[^{]*\{([^}]*)\}/);
    expect(markerRule, "no marker rule").toBeTruthy();
    expect(markerRule![1]).toContain("fill: rgb(var(--c-muted))");
  });

  it("colours nodes and text from the tokens, so the flip is free", () => {
    // The paint has to be `var(--c-…)` rather than a baked hex: that is what
    // recolours a diagram that is ALREADY on screen when the `dark` class moves.
    // Kills: hard-coding hex into these rules.
    expect(section).toContain("fill: rgb(var(--c-panel))");
    expect(section).toContain("stroke: rgb(var(--c-rail))");
    expect(section).toContain("fill: rgb(var(--c-ink))");
    // No literal colours in the section at all.
    expect(section).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });

  it("keeps `text-anchor: middle`, without which every label hangs off its node", () => {
    // Mermaid's own rule, and load-bearing: the label group is translated to the
    // node's centre. Kills: restating the colours and forgetting the geometry.
    expect(section).toContain("text-anchor: middle");
  });

  it("scrolls inside its own box instead of widening the chat column", () => {
    // Kills: letting an oversized diagram push the column wide, and kills the
    // `max-width: 100%` that used to scale one down instead.
    expect(section).toMatch(/\.mermaid-diagram \{[^}]*overflow-x: auto/);
    expect(buildMermaidConfig("light", paint(LIGHT)).flowchart?.useMaxWidth).toBe(false);
  });

  it("adds no animation — mermaid's own sheet carries a marching-ants keyframe", () => {
    // Nothing in this app moves without asking `prefers-reduced-motion` first, and
    // the cheapest way to keep that true here is to animate nothing.
    expect(section).not.toContain("animation");
    expect(section).not.toContain("@keyframes");
    expect(section).not.toContain("transition:");
  });
});

// ===========================================================================
// 4. The component: initialized per palette, re-rendered on the flip
// ===========================================================================

describe("MermaidDiagram follows the appearance flip", () => {
  beforeEach(() => {
    vi.resetModules();
    document.documentElement.classList.remove("dark");
  });

  afterEach(() => {
    document.documentElement.classList.remove("dark");
    vi.doUnmock("mermaid");
  });

  /** A mermaid stand-in that records what it was initialized and asked to draw
   * with. The real one cannot run here — it needs `getBBox`, which jsdom has not
   * got — so the seam under test is the call, not the picture. */
  function mockMermaid() {
    const inits: { darkMode?: boolean; background?: string }[] = [];
    const renders: string[] = [];
    vi.doMock("mermaid", () => ({
      default: {
        initialize: (config: { darkMode?: boolean; themeVariables?: ThemeVars }) =>
          inits.push({
            darkMode: config.darkMode,
            background: config.themeVariables?.background,
          }),
        render: async (id: string) => {
          renders.push(id);
          return { svg: "<svg><text>diagram</text></svg>" };
        },
      },
    }));
    return { inits, renders };
  }

  it("initializes against the palette the app has painted", async () => {
    // Kills: reading the class once at module load, and kills defaulting to light
    // in a dark session.
    document.documentElement.classList.add("dark");
    const { inits } = mockMermaid();
    const { MermaidDiagram } = await import("../components/MermaidDiagram");
    render(<MermaidDiagram code="graph TD; A-->B" />);
    await waitFor(() => expect(inits.length).toBeGreaterThan(0));
    expect(inits[0].darkMode).toBe(true);
  });

  it("re-initializes and re-renders when the theme flips under it", async () => {
    // THE FLIP (§09 of the manual pass expects surfaces to recolour with the app).
    // What is on screen recolours from CSS on its own; this is about the NEXT
    // measurement being taken against the palette that is live. Kills: the
    // once-per-session `initialized` flag this component shipped with, and kills a
    // component that never learns the class moved.
    const { inits, renders } = mockMermaid();
    const { MermaidDiagram } = await import("../components/MermaidDiagram");
    render(<MermaidDiagram code="graph TD; A-->B" />);
    await waitFor(() => expect(inits).toHaveLength(1));
    expect(inits[0].darkMode).toBe(false);
    const first = renders.length;

    document.documentElement.classList.add("dark");
    await waitFor(() => expect(inits).toHaveLength(2));
    expect(inits[1].darkMode).toBe(true);
    // Re-rendered too, with a FRESH id — mermaid keys its work by the id it is
    // given, so reusing one is how the second draw silently becomes a no-op.
    await waitFor(() => expect(renders.length).toBeGreaterThan(first));
    expect(new Set(renders).size).toBe(renders.length);
  });

  it("keeps the drawn diagram up across the flip rather than blanking it", async () => {
    // A flip changes no geometry — the paint is CSS — so clearing the SVG would
    // trade a correct picture for a "Preparing diagram…" flash. Kills: resetting
    // the state at the top of the render effect.
    mockMermaid();
    const { MermaidDiagram } = await import("../components/MermaidDiagram");
    const { container } = render(<MermaidDiagram code="graph TD; A-->B" />);
    await waitFor(() => expect(container.querySelector("text")).toBeTruthy());
    document.documentElement.classList.add("dark");
    expect(container.querySelector("text")).toBeTruthy();
  });
});
