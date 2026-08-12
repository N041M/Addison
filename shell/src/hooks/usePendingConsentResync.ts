// The consent re-sync watchdog (KNOWN-BUGS P2 #3).
//
// `permission.requestGrant` is a NOTIFICATION, and a notification has no second
// chance. The IPC client drops one that arrives with no subscriber; the webview can
// clear the card it produced (a conversation switch used to); a card can be rendered
// somewhere the reader never looks. Every one of those ends in the same place — the
// engine blocked on an answer, "Working…" on screen, and NOTHING anywhere that
// expires. That is the 2026-08-09 sighting: four minutes, no card, no timeout.
//
// So the surface asks. While a turn is working with no card on screen, this polls
// `permission.pending` and hands back whatever the engine is waiting for.
//
// WHAT IT IS AND IS NOT. It is not the fix for the race that was actually found —
// ChatThread's `attention` prop is (the card was in the DOM, below the fold of a
// container with a hidden scrollbar). It is the net under every OTHER way that frame
// can go missing, including ones nobody has diagnosed yet, and it turns each of them
// into a two-second blip instead of a wait with no end. A watchdog that is also the
// only fix is a watchdog nobody notices firing.
//
// It only ever READS. It cannot answer a card, and asking costs an arming card no
// attempt (agent_core/main.py, `_handle_permission_pending`).

import { useEffect, useRef } from "react";
import { ipc } from "../ipc/client";
import { asRecord } from "../lib/parse";

/** How often a working turn with no card on screen asks. Short enough that a lost
 * notification is a blip rather than a wait — the sighting it answers ran four
 * minutes — and long enough that it is one tiny store-free read every two seconds,
 * only ever while a turn is actually running. */
export const PENDING_RESYNC_MS = 2000;

interface Args {
  /**
   * Whether the engine could be blocked on a card the surface is not showing:
   * connected, a turn running, and no card rendered. A running turn is the only
   * state in which that is possible, so an idle app asks a question whose answer is
   * always no — and never asks it.
   */
  enabled: boolean;
  /** Called with the raw card the engine is waiting on. App normalises + renders it. */
  onFound: (request: Record<string, unknown>) => void;
}

export function usePendingConsentResync({ enabled, onFound }: Args): void {
  // Held in a ref so a caller that rebuilds the callback every render (App does)
  // cannot restart the interval — a timer that resets on every render never fires.
  const onFoundRef = useRef(onFound);
  onFoundRef.current = onFound;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const timer = setInterval(() => {
      ipc
        .pendingPermission()
        .then((res) => {
          if (cancelled) return;
          const request = asRecord(asRecord(res)?.request);
          // Nothing pending is the ordinary answer, and it says nothing is wrong.
          if (!request) return;
          onFoundRef.current(request);
        })
        .catch(() => {
          /* the engine is busy or gone — the next tick asks again */
        });
    }, PENDING_RESYNC_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled]);
}
