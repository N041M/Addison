// A soft violet pool under the mouse pointer (owner request, 2026-07-26).
//
// It is deliberately at the very bottom of the stack, lighting the paper rather
// than the text: `z-0` behind the whole app, so any opaque surface — a popover,
// the modal, the drawer — simply covers it. Nothing it does can reduce the
// contrast of a word on screen.
//
// THREE THINGS IT MUST NOT DO, which is most of why this is a component rather
// than a few lines in App:
//
//   * Re-render anything. The pointer position is written straight onto the
//     element's transform inside a rAF; it never touches React state. A
//     mousemove that re-rendered the app would be a real cost on a long thread,
//     which is the exact opposite of what the windowing work was for.
//   * Run when motion is off. `prefers-reduced-motion` (and the module flag)
//     turns it off completely rather than merely stopping the transition — a
//     light that tracks the pointer IS motion, whatever the transition says.
//   * Appear on a touch screen, where there is no pointer to sit under.
//
// The accent is otherwise reserved for actions, selection and live state, and
// this is decoration, so it runs at 6% and 3% — visible as a change in the paper
// when you look for it, not a thing you notice.

import { useEffect, useRef } from "react";
import { isMotionEnabled } from "../lib/scramble";

/** How far below the pointer the pool sits, in px — "under" the cursor. */
const DROP_PX = 10;

export function PointerGlow() {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!isMotionEnabled()) return;
    if (typeof window.matchMedia === "function" && !window.matchMedia("(pointer: fine)").matches) {
      return;
    }

    let x = 0;
    let y = 0;
    let frame = 0;
    let shown = false;

    const paint = () => {
      frame = 0;
      el.style.transform = `translate3d(${x}px, ${y + DROP_PX}px, 0)`;
      if (!shown) {
        shown = true;
        el.style.opacity = "1";
      }
    };

    const onMove = (event: PointerEvent) => {
      x = event.clientX;
      y = event.clientY;
      if (!frame) frame = requestAnimationFrame(paint);
    };

    // Leaving the window takes the light with it, so it never sits burning in a
    // corner of a window nobody is pointing at.
    const onLeave = () => {
      shown = false;
      el.style.opacity = "0";
    };

    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    window.addEventListener("blur", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      window.removeEventListener("blur", onLeave);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return <div ref={ref} aria-hidden="true" className="pointer-glow" />;
}
