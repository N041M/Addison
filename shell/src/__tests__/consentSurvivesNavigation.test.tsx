// A pending consent card must not be destroyed by a navigation — the second half
// of KNOWN-BUGS P2 #3's family.
//
// `handleNewChat` has always refused while the app is busy, and the sidebar
// disables the New-chat control to match. Opening ANOTHER CHAT did neither: the
// rows are live at all times, and `handleOpenConversation` ran straight into
// `resetTransientState()`, which clears the pending permission. That deleted the
// only copy of a question the ENGINE IS STILL BLOCKED ON — the notification is
// gone, nothing re-sends it, and nothing times out. Worse than the card being
// invisible: the worker thread stays parked inside `_ask_once`, so the next
// message the person sends queues behind it and the app looks dead.
//
// The two states are deliberately NOT treated alike. Leaving a running turn is
// ordinary and recoverable (the turn ref drops the late result). Leaving a card is
// not, so this refuses on the pending card alone and says why in plain words
// rather than dead-ending the click.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, cleanup } from "@testing-library/react";
import { useConversations } from "../hooks/useConversations";
import { ipc } from "../ipc/client";

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    ipc: {
      ...actual.ipc,
      listConversations: vi.fn(async () => ({ conversations: [] })),
      loadConversation: vi.fn(async () => ({
        conversationId: "c2",
        title: "Another chat",
        messages: [],
      })),
    },
  };
});

const loadConversation = ipc.loadConversation as ReturnType<typeof vi.fn>;

function setup(permissionPending: boolean) {
  const resetTransientState = vi.fn();
  const setStatusBanner = vi.fn();
  const hook = renderHook(() =>
    useConversations({
      connected: true,
      controlsBusy: true, // a turn is running either way
      permissionPending,
      resetTransientState,
      setMessages: vi.fn(),
      setScreen: vi.fn(),
      setStatusBanner,
    }),
  );
  return { hook, resetTransientState, setStatusBanner };
}

beforeEach(() => {
  loadConversation.mockClear();
});

afterEach(cleanup);

describe("opening another chat while the engine waits for consent", () => {
  it("refuses, and never clears the card", async () => {
    const { hook, resetTransientState, setStatusBanner } = setup(true);

    await act(async () => {
      hook.result.current.handleOpenConversation("c2");
    });

    expect(loadConversation).not.toHaveBeenCalled();
    // The line that matters: this is what used to delete the pending card.
    expect(resetTransientState).not.toHaveBeenCalled();
    // Not a dead click — the person is told what the app is waiting for, in the
    // card's own terms and with no jargon (personas 54 and 68).
    const said = setStatusBanner.mock.calls[0]?.[0] as string;
    expect(said).toMatch(/answer/i);
    expect(said).toMatch(/waiting/i);
  });

  it("still opens the chat when only a turn is running", async () => {
    // The guard must be about the CARD, not about being busy: stranding a turn's
    // result is recoverable and has always been allowed.
    const { hook, resetTransientState, setStatusBanner } = setup(false);

    await act(async () => {
      hook.result.current.handleOpenConversation("c2");
    });

    expect(loadConversation).toHaveBeenCalledWith("c2");
    expect(resetTransientState).toHaveBeenCalled();
    expect(setStatusBanner).not.toHaveBeenCalled();
  });
});
