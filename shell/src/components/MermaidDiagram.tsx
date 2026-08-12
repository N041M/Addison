// Display-only Mermaid diagram rendering for assistant messages.
//
// Two hard constraints shape this component:
//   1. Mermaid is ~2MB. It must NOT sit in the initial bundle — it's pulled in
//      with a dynamic `import("mermaid")` inside an effect, so Vite code-splits
//      it into its own lazy chunk that only loads when a diagram actually
//      appears in a message.
//   2. It renders under `securityLevel: "strict"`, and the SVG it hands back is
//      sanitized by mermaid itself. We keep it strictly display-only — no
//      interaction, no script, no navigation.
//
// A malformed diagram must never break a message row: any parse/render failure
// falls back to showing the original fenced code as a plain <pre><code> block.
//
//   3. THE CSS IN THAT SVG IS STRIPPED BEFORE IT IS INJECTED (2026-08-08). The
//      app's CSP now carries `style-src 'self' 'unsafe-inline'` so that Monaco can
//      position its view lines (Phase-3 review surface), and this component is the
//      app's ONLY path for markup it did not author. A diagram's `classDef` becomes
//      a `<style>` block, which applies to the whole document and could therefore
//      select the permission card; `style A fill:…` becomes a style attribute,
//      which could pin one node over the window. Both were blocked by the old
//      policy, so removing them keeps diagrams looking exactly as they do today
//      while the widening changes nothing here — see lib/sanitizeSvg.ts, which owns
//      the reasoning and what it does not cover.

//   4. IT IS NOT MERMAID'S THEME ON SCREEN (2026-08-11, KNOWN-BUGS #15). Because
//      of 3, the `<style>` block that carries mermaid's whole palette is stripped
//      before injection — so the SVG defaults took over and diagrams rendered
//      black-filled, black-texted, with every edge painted as a filled blob
//      (`.flowchart-link { fill: none }` went with the stylesheet). The paint now
//      lives in `src/styles.css` under `.mermaid-diagram`, reading the same
//      `--c-*` variables as the rest of the app, which is also what makes a theme
//      flip recolour a diagram that is already drawn. `lib/mermaidTheme.ts` owns
//      the other half — the config, above all the font mermaid MEASURES with.

import { useEffect, useState } from "react";
import { stripInjectedCss } from "../lib/sanitizeSvg";
import { buildMermaidConfig } from "../lib/mermaidTheme";
import { readDocumentTheme, type ResolvedTheme } from "../lib/theme";

// Which palette mermaid was last initialized against, module-wide: initializing
// is per-session state in the library, not per-component. `null` = never.
let initializedFor: ResolvedTheme | null = null;

// Monotonic id source: mermaid needs a unique DOM id per render call.
let renderSeq = 0;

interface Props {
  code: string;
}

/**
 * The palette the app has painted, as a value that changes when it changes.
 *
 * `monacoTheme.ts` argues against a `MutationObserver` on the `dark` class, and is
 * right for the editor: App already computes the resolved theme and hands it to
 * `CodeSurface` as a prop. A diagram cannot be reached that way — it is created
 * inside model-authored markdown, several layers below any component that knows
 * the theme, and threading a prop through `ChatThread` → `Markdown` →
 * `ReactMarkdown`'s component map to arrive here would put the appearance setting
 * into the signature of every message renderer in the app. What this observes is
 * not a second computation of the CHOICE (that stays App's, `resolveTheme`), it is
 * the class App itself wrote — the app's own output, read back.
 */
function useDocumentTheme(): ResolvedTheme {
  const [theme, setTheme] = useState<ResolvedTheme>(() => readDocumentTheme());

  useEffect(() => {
    const root = document.documentElement;
    const sync = () => setTheme(readDocumentTheme(root));
    // The class can have moved between the first render and this effect.
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return theme;
}

export function MermaidDiagram({ code }: Props) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const theme = useDocumentTheme();

  // One render per `code` value AND per palette: the effect re-runs when either
  // changes, and `cancelled` keeps a stale run from setting state. (No extra
  // "already rendered" ref guard here — under React 18 StrictMode the first dev
  // effect run is cancelled on the simulated unmount, and a ref guard would make
  // the second run bail too, leaving the placeholder up forever.)
  useEffect(() => {
    let cancelled = false;
    setFailed(false);

    (async () => {
      try {
        // Lazy, code-split import: keeps mermaid out of the initial chunk.
        const mermaid = await import("mermaid");
        if (initializedFor !== theme) {
          // Re-initialized on every flip, not once per session: mermaid bakes
          // `themeVariables` in at INITIALIZE time, so a diagram drawn after the
          // flip would otherwise be measured and marked up against the palette
          // the app has stopped painting. (What is on screen recolours from
          // `styles.css` regardless — see the file header.)
          mermaid.default.initialize(buildMermaidConfig(theme));
          initializedFor = theme;
        }
        const id = `addison-mermaid-${(renderSeq += 1)}`;
        const { svg: out } = await mermaid.default.render(id, code);
        // Sanitize on the way OUT of the render, not at the injection site: the
        // component then holds only markup that is already safe to inject, so a
        // second render path added later cannot forget the step by using the
        // state directly.
        if (!cancelled) setSvg(stripInjectedCss(out));
      } catch {
        // Parse failures are common and expected; degrade to plain code.
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
    // The previous SVG is deliberately NOT cleared at the top of this effect: a
    // theme flip re-renders a diagram that is already on screen and correctly
    // coloured (the paint is CSS), so blanking it to "Preparing diagram…" for a
    // frame would be a flash of nothing in exchange for no change at all.
  }, [code, theme]);

  if (failed) {
    return (
      <pre>
        <code>{code}</code>
      </pre>
    );
  }

  if (svg == null) {
    // Quiet, non-flashy placeholder while the lazy chunk loads and renders.
    return <pre className="text-muted">Preparing diagram…</pre>;
  }

  // The SVG is mermaid's own output, sanitized by mermaid under
  // securityLevel: "strict" and then stripped of every stylesheet, style
  // attribute and event handler by `stripInjectedCss` above. We inject it
  // display-only (no handlers, no links that navigate) — this is the one
  // sanctioned dangerouslySetInnerHTML use in the app, and a source-level test
  // fails when a second one appears.
  return (
    <div className="mermaid-diagram" aria-label="Diagram" dangerouslySetInnerHTML={{ __html: svg }} />
  );
}
