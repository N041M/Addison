// MobileDrawer — the left slide-over that hosts the Sidebar below the md
// breakpoint (Fern mobile layout, handoff mobile bullet §1). Desktop keeps the
// static 216px sidebar; on a narrow window that column is hidden and the same
// Sidebar component slides in here (280px, `side` bg, a scrim behind it).
//
// This is CHROME around the existing Sidebar, not a fork: App renders
// `<MobileDrawer onClose={…}><Sidebar variant="drawer" …/></MobileDrawer>` and
// wraps the Sidebar's pick handlers so choosing a conversation / Settings / New
// chat closes the drawer. Scrim tap closes it here; Escape is handled globally
// in App. The slide-in is a 250ms animation that the reduced-motion rule in
// styles.css turns into an instant appearance.

import type { ReactNode } from "react";

interface Props {
  onClose: () => void;
  children: ReactNode;
}

export function MobileDrawer({ onClose, children }: Props) {
  return (
    <div className="fixed inset-0 z-40 md:hidden">
      {/* Scrim: ink at 25% (matches the design reference). Tap to dismiss. */}
      <div
        className="absolute inset-0 bg-ink/25 animate-[fade-in_200ms_ease]"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* The slide-over panel. Width 280px, capped so it never covers the whole
          narrow screen; the Sidebar (variant="drawer") fills it. */}
      <div className="absolute inset-y-0 left-0 flex w-[280px] max-w-[82%] animate-[drawer-in_250ms_ease]">
        {children}
      </div>
    </div>
  );
}
