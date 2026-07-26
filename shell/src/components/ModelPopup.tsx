// The anchored model popup — Settings → "Where Addison thinks" → the Cloud model
// row's "change" (docs/design-brief-dark, "Model select popup"; prototype.html
// ~172–182 + `modelPop*` in renderVals ~627–635).
//
// A fixed-position 270px panel that opens AT THE CLICK, macOS-select style: the
// SELECTED row lands under the pointer, so changing your mind is a small
// movement rather than a hunt. That is the whole reason for the arithmetic below
// — x = click.right − 250, y = click.centre − 14 − selectedIndex × 29 — and for
// the clamp that keeps the panel ≥12px inside the viewport when the selected row
// is far down the list or the trigger sits near an edge.
//
// ROWS ARE THE REAL CATALOG, exactly as in the composer's menu (ModelSelector):
// cloud models from the connected providers plus whatever is set up under the
// local role. `free` is never inferred — the note says "free" only when the core
// itself flagged the model, because no cloud model may claim to cost nothing on
// the frontend's authority (CLAUDE.md, Phase-2 step 4).

import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** Panel geometry — the prototype's numbers, kept as named constants because the
 * positioning maths reads as nonsense without them. */
const PANEL_WIDTH = 270;
/** How far left of the click the panel's left edge sits (prototype: x − 250). */
const ANCHOR_INSET = 250;
/** Half a row's text height — centres the selected row on the click point. */
const ROW_HALF = 14;
/** One row's height, for stepping the panel up past the selected index. */
const ROW_STEP = 29;
/** Never closer than this to any viewport edge. */
const EDGE_MARGIN = 12;

export interface ModelPopupOption {
  /** Unique across cloud + local (the role is part of it for local rows). */
  key: string;
  label: string;
  /** "quality" | "free" | "local" — derived from the CORE's flags, never guessed. */
  note: string;
  selected: boolean;
  onPick: () => void;
}

export interface PopupAnchor {
  /** The trigger's right edge, in viewport coordinates. */
  x: number;
  /** The trigger's vertical centre, in viewport coordinates. */
  y: number;
}

export function ModelPopup({
  anchor,
  options,
  onClose,
}: {
  anchor: PopupAnchor;
  options: ModelPopupOption[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const selectedIndex = Math.max(
    0,
    options.findIndex((o) => o.selected),
  );
  // Measured after paint so the bottom clamp uses the panel's real height rather
  // than a guess from the row count (a wrapped label makes a row taller).
  const [height, setHeight] = useState(0);
  useLayoutEffect(() => {
    setHeight(ref.current?.offsetHeight ?? 0);
  }, [options.length]);

  const viewportW = typeof window === "undefined" ? PANEL_WIDTH + 2 * EDGE_MARGIN : window.innerWidth;
  const viewportH = typeof window === "undefined" ? 0 : window.innerHeight;

  const rawX = anchor.x - ANCHOR_INSET;
  const rawY = anchor.y - ROW_HALF - selectedIndex * ROW_STEP;
  const left = clamp(rawX, EDGE_MARGIN, Math.max(EDGE_MARGIN, viewportW - PANEL_WIDTH - EDGE_MARGIN));
  const top = clamp(
    rawY,
    EDGE_MARGIN,
    Math.max(EDGE_MARGIN, viewportH - (height || 0) - EDGE_MARGIN),
  );

  // Outside click and Escape both close. Escape is handled here, and stops
  // propagating, so it closes the popup rather than leaving the whole surface.
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      role="listbox"
      aria-label="Which cloud model Addison uses by default"
      style={{ left: `${left}px`, top: `${top}px`, width: `${PANEL_WIDTH}px` }}
      className="fixed z-50 animate-[fade_.12s_ease_both] rounded-popover bg-panel px-4 py-1.5 shadow-popover"
    >
      {options.map((o, i) => (
        <div
          key={o.key}
          role="option"
          aria-selected={o.selected}
          tabIndex={0}
          onClick={o.onPick}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              o.onPick();
            }
          }}
          className={
            "flex cursor-pointer items-baseline gap-2.5 border-l-2 border-t py-3 pl-3 pr-0.5 " +
            "text-[12px] transition-colors hover:text-ink " +
            (i === 0 ? "border-t-transparent " : "border-t-line ") +
            (o.selected ? "border-l-accent text-ink" : "border-l-transparent text-ink-soft")
          }
        >
          <span className="min-w-0 truncate">{o.label}</span>
          <span className="flex-1" />
          <span
            className={
              "shrink-0 font-mono text-[10.5px] " + (o.selected ? "text-accent" : "text-muted")
            }
          >
            {o.note}
          </span>
        </div>
      ))}
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
