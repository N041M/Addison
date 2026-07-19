// MobileDrawer — the left slide-over that hosts the Sidebar below the md
// breakpoint (Fern mobile layout, handoff mobile bullet §1). Desktop keeps the
// static 216px sidebar; on a narrow window that column is hidden and the same
// Sidebar component slides in here (280px, `side` bg, a scrim behind it).
//
// This is CHROME around the existing Sidebar, not a fork: App renders
// `<MobileDrawer open={drawerOpen} onClose={…}><Sidebar variant="drawer" …/></MobileDrawer>`.
// Every close path just flips `open` false — the scrim tap, the drawer's own
// close arrow, Escape (handled in App), and a conversation/Settings/Widgets
// pick. The drawer OWNS the animation both ways: it slides + fades in on open,
// and on close it stays mounted to play the slide-out, unmounting only when that
// animation ends. Under prefers-reduced-motion no animation runs (so no
// animationend fires) — the effect detects that and unmounts instantly instead.

import { useEffect, useState, type ReactNode } from "react";

interface Props {
  /** Whether the drawer should be shown. Flipping this false plays the exit. */
  open: boolean;
  /** A close request (scrim tap): just flip `open` false in the parent. */
  onClose: () => void;
  children: ReactNode;
}

export function MobileDrawer({ open, onClose, children }: Props) {
  // Stay mounted through the exit animation after `open` flips false.
  const [rendered, setRendered] = useState(open);
  const [closing, setClosing] = useState(false);

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

  if (!rendered) return null;

  // Fires when the panel's own slide-out finishes (guarded by name so a
  // bubbled child animationend can't unmount us early).
  const onPanelAnimationEnd = (e: React.AnimationEvent<HTMLDivElement>) => {
    if (closing && e.animationName === "drawer-out") {
      setRendered(false);
      setClosing(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 md:hidden">
      {/* Scrim: ink at 25% (matches the design reference). Tap to dismiss. */}
      <div
        className={
          "absolute inset-0 bg-ink/25 " +
          (closing ? "animate-[fade-out_200ms_ease_forwards]" : "animate-[fade-in_200ms_ease]")
        }
        onClick={onClose}
        aria-hidden="true"
      />
      {/* The slide-over panel. Width 280px, capped so it never covers the whole
          narrow screen; the Sidebar (variant="drawer") fills it. `forwards` on
          the exit holds it off-screen until unmount, so there's no snap-back. */}
      <div
        className={
          "absolute inset-y-0 left-0 flex w-[280px] max-w-[82%] " +
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
