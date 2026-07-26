// MobileDrawer — the left slide-over that hosts the Sidebar below the md
// breakpoint (docs/design-brief-dark, "Layout & chrome → Mobile": keep the
// existing drawer structure, restyled to the tokens). Desktop keeps the static
// sidebar column; on a narrow window that column is hidden and the same Sidebar
// component slides in here (280px, `paper` bg from Sidebar's drawer variant, a
// scrim behind it).
//
// This is CHROME around the existing Sidebar, not a fork: App renders
// `<MobileDrawer open={drawerOpen} onClose={…}><Sidebar variant="drawer" …/></MobileDrawer>`.
// Every close path just flips `open` false — the scrim tap, the drawer's own
// close arrow, Escape (handled in App), and a conversation/Settings/Widgets
// pick. The drawer OWNS the animation both ways: it slides + fades in on open,
// and on close it stays mounted to play the slide-out, unmounting only when that
// animation ends. Under prefers-reduced-motion no animation runs (so no
// animationend fires) — the effect detects that and unmounts instantly instead.
//
// IT IS A MODAL, so it carries the semantics of one: role="dialog" +
// aria-modal, focus moves into the panel on open and back to the ☰ on close,
// and Tab cycles inside it. Without that, a screen-reader or keyboard user
// tabbed straight through the page BEHIND the scrim — a menu they could not see
// and controls they could not tell were covered.

import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";

interface Props {
  /** Whether the drawer should be shown. Flipping this false plays the exit. */
  open: boolean;
  /** A close request (scrim tap): just flip `open` false in the parent. */
  onClose: () => void;
  children: ReactNode;
}

/** Everything Tab would normally stop on, in DOM order. */
const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),' +
  'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

export function MobileDrawer({ open, onClose, children }: Props) {
  // Stay mounted through the exit animation after `open` flips false.
  const [rendered, setRendered] = useState(open);
  const [closing, setClosing] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Where focus was when the drawer opened — the ☰ in the header — so closing
  // puts the person back where they pressed rather than at the top of the page.
  const returnFocusTo = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (open) {
      setRendered(true);
      setClosing(false);
      return;
    }
    if (!rendered) return; // already gone — nothing to animate out
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) {
      setRendered(false); // no animationend will fire — close instantly
      return;
    }
    setClosing(true); // play the slide-out; onAnimationEnd unmounts
  }, [open, rendered]);

  // Move focus into the panel as it appears. Runs after the paint that mounted
  // it, so the first control is really in the DOM by now.
  useEffect(() => {
    if (!open || !rendered) return;
    returnFocusTo.current = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    (panel?.querySelector<HTMLElement>(FOCUSABLE) ?? panel)?.focus();
  }, [open, rendered]);

  // …and hand it back once the drawer is really gone (after the slide-out).
  useEffect(() => {
    if (open || rendered) return;
    returnFocusTo.current?.focus();
    returnFocusTo.current = null;
  }, [open, rendered]);

  if (!rendered) return null;

  // Tab stays inside the panel. Everything behind the scrim is covered and
  // untappable, so letting Tab walk into it would move focus somewhere the
  // person cannot see. Escape (handled in App) is the way out.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const stops = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (stops.length === 0) return;
    e.preventDefault();
    const i = stops.indexOf(document.activeElement as HTMLElement);
    const next = e.shiftKey
      ? stops[(i <= 0 ? stops.length : i) - 1]
      : stops[(i + 1) % stops.length];
    next.focus();
  };

  // Fires when the panel's own slide-out finishes (guarded by name so a
  // bubbled child animationend can't unmount us early).
  const onPanelAnimationEnd = (e: React.AnimationEvent<HTMLDivElement>) => {
    if (closing && e.animationName === "drawer-out") {
      setRendered(false);
      setClosing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 md:hidden" onKeyDown={onKeyDown}>
      {/* Scrim: the shared `scrim` token — rgba(0,0,0,.55), the same value the
          Restore points modal uses. It used to be `ink/25`, which is 25% of the
          TEXT color: in dark that is near-white over near-black, so the drawer
          BRIGHTENED the app behind it (measured composite rgb(67,67,68)) instead
          of pushing it back. Tap to dismiss. */}
      <div
        className={
          "absolute inset-0 bg-scrim " +
          (closing ? "animate-[fade-out_200ms_ease_forwards]" : "animate-[fade-in_200ms_ease]")
        }
        onClick={onClose}
        aria-hidden="true"
      />
      {/* The slide-over panel. Width 280px, capped so it never covers the whole
          narrow screen; the Sidebar (variant="drawer") fills it. `forwards` on
          the exit holds it off-screen until unmount, so there's no snap-back. */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        // Focus target of last resort: if the Sidebar ever renders with no
        // control in it, focus still lands in the dialog rather than staying
        // behind the scrim.
        tabIndex={-1}
        className={
          "absolute inset-y-0 left-0 flex w-[280px] max-w-[82%] outline-none " +
          (closing
            ? "animate-[drawer-out_220ms_ease_forwards]"
            : "animate-[drawer-in_250ms_ease]")
        }
        onAnimationEnd={onPanelAnimationEnd}
      >
        {children}
      </div>
    </div>
  );
}
