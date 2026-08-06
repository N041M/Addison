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

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { isMotionEnabled } from "../lib/scramble";
import { initialCollapsedGroups, modelListRows } from "../lib/modelGroups";

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
  /**
   * Which company this model comes from — "Anthropic", "Google", "On this
   * computer". Rows are drawn under a heading per group, so a picker holding
   * four providers' models reads as four short lists instead of one long one.
   *
   * Callers pass options ALREADY GROUPED (all of a company's rows adjacent). The
   * heading is emitted wherever this value changes rather than by bucketing here,
   * so the order the caller chose is the order drawn — the menu must never
   * reshuffle itself out from under a keyboard position the caller is tracking.
   */
  group?: string;
  /** "quality" | "free" | "local" — derived from the CORE's flags, never guessed. */
  note: string;
  /** The provider has refused this model before (core-observed, never inferred). */
  unavailable?: boolean;
  selected: boolean;
  onPick: () => void;
}

export interface PopupAnchor {
  /** The trigger's right edge, in viewport coordinates. */
  x: number;
  /** The trigger's vertical centre, in viewport coordinates. */
  y: number;
}

/** How long the popup takes to fade out. Matches the .12s it fades in on. */
const EXIT_MS = 120;

export function ModelPopup({
  anchor,
  options,
  open = true,
  onClose,
}: {
  anchor: PopupAnchor;
  options: ModelPopupOption[];
  /**
   * False starts the close animation; the popup unmounts itself when it
   * finishes. The exit lives HERE rather than at the call site because several
   * things close this popup — Escape, an outside click, picking a model, a
   * profile change — and a caller that forgot would put the old behaviour back
   * for that one path. It faded in and then vanished on a frame (reported
   * 2026-07-26: "it just plops out").
   */
  open?: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Which companies are folded shut. Seeded ONCE per mount from the options, so
  // reopening the menu forgets what was expanded last time — the panel is a
  // glance surface, and a stale expansion is a stale answer to "what is on?".
  const [collapsed, setCollapsed] = useState(() =>
    initialCollapsedGroups(options, (o) => o.selected),
  );
  const toggle = (group: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (!next.delete(group)) next.add(group);
      return next;
    });
  const rows = useMemo(() => modelListRows(options, collapsed), [options, collapsed]);
  // Kept up for the length of the exit, then gone.
  const [exiting, setExiting] = useState(false);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      setExiting(false);
      return;
    }
    if (!wasOpen.current) return;
    wasOpen.current = false;
    if (!isMotionEnabled()) return;
    setExiting(true);
    const timer = setTimeout(() => setExiting(false), EXIT_MS);
    return () => clearTimeout(timer);
  }, [open]);
  // Derived rather than state, so the panel exists on the render that opens it.
  const present = open || exiting;
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
    if (!open) return; // a closing popup answers nothing
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
  }, [onClose, open]);

  if (!present) return null;

  return (
    <div
      ref={ref}
      role="listbox"
      aria-label="Which cloud model Addison uses by default"
      // Out of the a11y tree the moment it starts closing — see ModelSelector.
      aria-hidden={!open}
      style={{ left: `${left}px`, top: `${top}px`, width: `${PANEL_WIDTH}px` }}
      className={
        "fixed z-50 rounded-popover bg-panel px-4 py-1.5 shadow-popover " +
        (open
          ? "animate-[fade_.12s_ease_both]"
          : "pointer-events-none animate-[fade-out_.12s_ease_both]")
      }
    >
      {rows.map((row, i) => {
        if (row.kind === "heading") {
          return (
            <button
              key={`h:${row.group}`}
              type="button"
              // Presentational to the LISTBOX — a heading announced as an option
              // would be an unpickable row — but a real button, because it does
              // something. `aria-expanded` is the part that carries the state.
              aria-expanded={!row.collapsed}
              onClick={() => toggle(row.group)}
              className={
                "flex w-full items-baseline gap-2 px-3 pb-1.5 text-left font-mono " +
                "text-[10.5px] uppercase tracking-wide text-muted transition-colors " +
                "hover:text-ink-soft " +
                (i === 0 ? "pt-2" : "border-t border-t-line pt-3")
              }
            >
              <span className="min-w-0 truncate">{row.group}</span>
              <span className="flex-1" />
              <span className="shrink-0 normal-case tracking-normal">
                {row.collapsed ? row.total : "collapse"}
              </span>
            </button>
          );
        }
        if (row.kind === "more") {
          return (
            <button
              key={`m:${row.group}`}
              type="button"
              onClick={() => toggle(row.group)}
              className={
                "flex w-full cursor-pointer items-baseline border-l-2 border-l-transparent " +
                "border-t border-t-line py-3 pl-3 pr-0.5 text-left font-mono text-[10.5px] " +
                "text-muted transition-colors hover:text-ink-soft"
              }
            >
              {row.hidden} more…
            </button>
          );
        }
        const o = row.option;
        const first = rows[i - 1]?.kind === "heading";
        return (
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
              (i === 0 || first ? "border-t-transparent " : "border-t-line ") +
              (o.unavailable
                ? "border-l-transparent text-disabled"
                : o.selected
                  ? "border-l-accent text-ink"
                  : "border-l-transparent text-ink-soft")
            }
          >
            <span className={"min-w-0 truncate " + (o.unavailable ? "line-through" : "")}>
              {o.label}
            </span>
            <span className="flex-1" />
            <span
              className={
                "shrink-0 font-mono text-[10.5px] " + (o.selected ? "text-accent" : "text-muted")
              }
            >
              {o.note}
            </span>
          </div>
        );
      })}
      {/*
        A LISTED MODEL IS NOT A PROMISE, and the panel should say so before the
        turn does. A provider's own catalogue over-promises: Google lists
        `gemini-2.5-flash` advertising the right method, then refuses it with "no
        longer available to new users" — retired for keys made after a cutoff and
        still on the list. Nothing visible distinguished it from a working model.

        Deliberately not a per-row badge: which models a key may use is not
        knowable until one is called, so a mark would be a guess on every row.
        One honest line under the list beats thirty confident ones.
      */}
      <div
        role="presentation"
        className="border-t border-t-line px-3 py-2 font-mono text-[10px] text-disabled"
      >
        not every model here works with every key
      </div>
    </div>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
