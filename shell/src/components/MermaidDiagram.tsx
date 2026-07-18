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

import { useEffect, useRef, useState } from "react";

// Initialize mermaid exactly once per session, no matter how many diagrams
// render. Guarded at module level so re-mounts don't re-initialize.
let initialized = false;

// Monotonic id source: mermaid needs a unique DOM id per render call.
let renderSeq = 0;

interface Props {
  code: string;
}

export function MermaidDiagram({ code }: Props) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  // Render at most once per distinct `code` value — no error flash, no retry
  // loop on a diagram that simply won't parse.
  const renderedFor = useRef<string | null>(null);

  useEffect(() => {
    if (renderedFor.current === code) return;
    renderedFor.current = code;

    let cancelled = false;
    setSvg(null);
    setFailed(false);

    (async () => {
      try {
        // Lazy, code-split import: keeps mermaid out of the initial chunk.
        const mermaid = await import("mermaid");
        if (!initialized) {
          // theme "dark" is deliberate: the app flips fully dark in the next PR.
          // Against the current light surface it reads slightly off for one PR —
          // accepted, and retuned when the dark restyle lands.
          mermaid.default.initialize({
            startOnLoad: false,
            securityLevel: "strict",
            theme: "dark",
          });
          initialized = true;
        }
        const id = `addison-mermaid-${(renderSeq += 1)}`;
        const { svg: out } = await mermaid.default.render(id, code);
        if (!cancelled) setSvg(out);
      } catch {
        // Parse failures are common and expected; degrade to plain code.
        if (!cancelled) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [code]);

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
  // securityLevel: "strict". We inject it display-only (no handlers, no links
  // that navigate) — this is the one sanctioned dangerouslySetInnerHTML use.
  return (
    <div className="mermaid-diagram" aria-label="Diagram" dangerouslySetInnerHTML={{ __html: svg }} />
  );
}
