// The consent re-sync watchdog — KNOWN-BUGS P2 #3, the belt-and-braces half.
//
// The mechanism that was actually diagnosed is fixed in ChatThread (a card
// appended to the thread's footer landed below the fold of a container whose
// scrollbar is hidden, and nothing scrolled to it — see chatThread.test.tsx).
// This is the net under everything else, because the delivery path has a property
// that makes any bug in it open-ended: `permission.requestGrant` is a
// NOTIFICATION. The IPC client drops one that arrives with no subscriber, the
// webview can clear the card it produced, and NOTHING on either side expires. So
// a lost frame is not a glitch, it is a permanent stall — four minutes in the
// sighting, and it would have been four hours.
//
// What is asserted here is therefore not "polling works" but the four properties
// that decide whether this net can be trusted and whether it can do harm:
//   1. a card the surface never received is picked up, and
//   2. within seconds, not minutes;
//   3. an idle app never asks (it would be asking a question whose answer is
//      always no), and neither does one already showing a card; and
//   4. a "nothing pending" answer, or a failed call, changes nothing at all.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import {
  usePendingConsentResync,
  PENDING_RESYNC_MS,
} from "../hooks/usePendingConsentResync";
import { ipc } from "../ipc/client";

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return { ...actual, ipc: { ...actual.ipc, pendingPermission: vi.fn() } };
});

const pendingPermission = ipc.pendingPermission as ReturnType<typeof vi.fn>;

// The card the core sends for an arming request (step 8 phase 3) — the shape from
// the sighting, and the one whose height is why it went off-screen in the first
// place. Passed through untouched: the hook hands back what the engine said.
const ARMING_CARD = {
  toolId: "arm_automation",
  label: "Arm this automation?",
  description: "Your computer will run this on its own.",
  riskTier: "high",
  arming: { nonce: "PALE-OTTER-91", attemptsLeft: 3 },
};

beforeEach(() => {
  vi.useFakeTimers();
  pendingPermission.mockReset();
  pendingPermission.mockResolvedValue({ request: null });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Advance past `n` poll intervals and let the promise continuations settle. */
async function tick(n = 1) {
  for (let i = 0; i < n; i++) {
    await act(async () => {
      vi.advanceTimersByTime(PENDING_RESYNC_MS);
    });
  }
}

describe("the pending-consent watchdog", () => {
  it("picks up a card the webview never received", async () => {
    pendingPermission.mockResolvedValue({ request: ARMING_CARD });
    const onFound = vi.fn();
    renderHook(() => usePendingConsentResync({ enabled: true, onFound }));

    await tick();
    expect(onFound).toHaveBeenCalledWith(ARMING_CARD);
  });

  it("takes seconds, not minutes", async () => {
    // The number itself is the point of this test. A stall the person sits through
    // is what the bug was; a re-sync measured in minutes would reproduce it.
    expect(PENDING_RESYNC_MS).toBeLessThanOrEqual(5000);

    pendingPermission.mockResolvedValue({ request: ARMING_CARD });
    const onFound = vi.fn();
    renderHook(() => usePendingConsentResync({ enabled: true, onFound }));

    await act(async () => {
      vi.advanceTimersByTime(PENDING_RESYNC_MS - 1);
    });
    expect(onFound).not.toHaveBeenCalled();
    await act(async () => {
      vi.advanceTimersByTime(1);
    });
    expect(onFound).toHaveBeenCalledTimes(1);
  });

  it("never asks while there is nothing it could be waiting for", async () => {
    // `enabled` is App's `connected && isWorking && no card on screen`. An idle
    // app polling the engine forever would be a cost with no question behind it.
    const onFound = vi.fn();
    renderHook(() => usePendingConsentResync({ enabled: false, onFound }));

    await tick(3);
    expect(pendingPermission).not.toHaveBeenCalled();
  });

  it("stops asking the moment a card is on screen, and resumes when it is answered", async () => {
    const onFound = vi.fn();
    const { rerender } = renderHook(
      ({ enabled }) => usePendingConsentResync({ enabled, onFound }),
      { initialProps: { enabled: true } },
    );

    await tick();
    expect(pendingPermission).toHaveBeenCalledTimes(1);

    // A card arrived (by either route): the watchdog has nothing to do.
    rerender({ enabled: false });
    await tick(2);
    expect(pendingPermission).toHaveBeenCalledTimes(1);

    // Answered, turn still running: watching resumes, from a FRESH interval — so
    // the first ask lands a full period after the answer went out, never in the
    // gap where the core has not yet marked that waiter answered.
    rerender({ enabled: true });
    await act(async () => {
      vi.advanceTimersByTime(PENDING_RESYNC_MS - 1);
    });
    expect(pendingPermission).toHaveBeenCalledTimes(1);
    await tick();
    expect(pendingPermission).toHaveBeenCalledTimes(2);
  });

  it("does nothing at all when the engine says nothing is pending", async () => {
    // The ordinary answer, several times a minute for the length of every turn.
    const onFound = vi.fn();
    renderHook(() => usePendingConsentResync({ enabled: true, onFound }));

    await tick(3);
    expect(pendingPermission).toHaveBeenCalledTimes(3);
    expect(onFound).not.toHaveBeenCalled();
  });

  it("survives a failed or malformed answer and keeps asking", async () => {
    // The engine is a separate process that can be restarting. A watchdog that
    // dies on the first refused call is one that is not there when it matters.
    pendingPermission.mockRejectedValueOnce(new Error("not connected"));
    pendingPermission.mockResolvedValueOnce({ request: "nonsense" });
    pendingPermission.mockResolvedValue({ request: ARMING_CARD });
    const onFound = vi.fn();
    renderHook(() => usePendingConsentResync({ enabled: true, onFound }));

    await tick(2);
    expect(onFound).not.toHaveBeenCalled();
    await tick();
    expect(onFound).toHaveBeenCalledWith(ARMING_CARD);
  });

  it("keeps polling across renders that rebuild the callback", async () => {
    // App passes a fresh arrow on every render. Holding it in state (or in the
    // effect's dependencies) would restart the interval each time — a timer that
    // resets more often than it fires never fires.
    pendingPermission.mockResolvedValue({ request: null });
    const { rerender } = renderHook(() =>
      usePendingConsentResync({ enabled: true, onFound: () => {} }),
    );

    await tick();
    rerender();
    rerender();
    await tick();
    expect(pendingPermission).toHaveBeenCalledTimes(2);
  });
});
