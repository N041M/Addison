// BottomSheet — the narrow-window home of the widget rail (Fern mobile layout,
// handoff mobile bullet §3). Below the md breakpoint the 270px rail column is
// hidden; the top bar's bell button opens this sheet, which renders the SAME
// WidgetRail content (variant="sheet") plus, above it, the "Undo last action"
// button that lives in the desktop chat header.
//
// Chrome only — it wraps whatever App passes as children (the undo row + the
// WidgetRail). Dismisses on: scrim tap, dragging the drag-handle down past a
// small threshold (a plain pointer-down + move check, no gesture library), and
// Escape (handled globally in App). The slide-up is a 250ms animation the
// reduced-motion rule in styles.css turns into an instant appearance.
//
// Safe-area: the sheet pads its bottom by env(safe-area-inset-bottom) (0 on
// desktop, the home-indicator inset on a phone shell).

import { useRef, type PointerEvent, type ReactNode } from "react";

interface Props {
  onClose: () => void;
  children: ReactNode;
}

// How far the handle must travel downward before we treat it as a dismiss.
const DISMISS_THRESHOLD_PX = 56;

export function BottomSheet({ onClose, children }: Props) {
  const startY = useRef<number | null>(null);

  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    startY.current = e.clientY;
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    if (startY.current == null) return;
    if (e.clientY - startY.current > DISMISS_THRESHOLD_PX) {
      startY.current = null;
      onClose();
    }
  }
  function onPointerUp() {
    startY.current = null;
  }

  return (
    <div className="fixed inset-0 z-40 flex flex-col justify-end md:hidden">
      {/* Scrim: ink at 25%. Tap to dismiss. */}
      <div
        className="absolute inset-0 bg-ink/25 animate-[fade-in_200ms_ease]"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Your widgets"
        className="relative flex max-h-[70vh] flex-col rounded-t-banner bg-surface pb-[env(safe-area-inset-bottom)] shadow-[0_-8px_28px_rgba(34,38,31,0.18)] animate-[sheet-in_250ms_ease]"
      >
        {/* Drag handle: a 36x4px hair bar with a comfortable touch area. */}
        <div
          className="flex shrink-0 cursor-grab touch-none justify-center pb-1.5 pt-2.5"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          aria-hidden="true"
        >
          <span className="h-1 w-9 rounded-pill bg-hair" />
        </div>
        <div className="flex min-h-0 flex-1 flex-col px-4">{children}</div>
      </div>
    </div>
  );
}
