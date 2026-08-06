// useWidgets' state write is OPTIMISTIC, then RECONCILED (Phase-2 step 6, half
// A). A checkbox that waits for a round trip feels broken, so the row changes at
// once — but "at once" is a promise the frontend makes on the core's behalf, and
// the two ways that promise can go bad are what these tests pin:
//
//   * the core stores something different from what was sent (it validates and
//     it, not this component, is the authority) — the row must end up on the
//     CORE's value, not on the hopeful one;
//   * the core refuses — the row must not be left showing a tick that does not
//     exist anywhere. It is re-read rather than patched from memory, because
//     after a failed write the truth is whatever the core holds.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useWidgets } from "../hooks/useWidgets";
import { ipc } from "../ipc/client";
import type { Widget } from "../types/ui";

vi.mock("../ipc/client", async () => {
  // parseWidgetState is real: the reconcile step is only meaningful if the
  // hook's answer goes through the same fail-closed parser the list does.
  const real = await vi.importActual<typeof import("../ipc/client")>("../ipc/client");
  return {
    ...real,
    isEngineConnected: () => true,
    ipc: {
      listWidgets: vi.fn(),
      listRoutines: vi.fn(),
      getStats: vi.fn(),
      setWidgetState: vi.fn(),
    },
  };
});

const listWidgets = ipc.listWidgets as unknown as ReturnType<typeof vi.fn>;
const listRoutines = ipc.listRoutines as unknown as ReturnType<typeof vi.fn>;
const setWidgetState = ipc.setWidgetState as unknown as ReturnType<typeof vi.fn>;

const CHECKLIST: Widget = {
  id: "w-check",
  pinned: true,
  spec: { kind: "checklist", items: ["Buy milk", "Call Ana"], title: "Saturday" },
  state: { checked: [false, false] },
};

function setup() {
  const setStatusBanner = vi.fn();
  const hook = renderHook(() =>
    useWidgets({ connected: true, railOpen: true, setStatusBanner }),
  );
  return { hook, setStatusBanner };
}

beforeEach(() => {
  listWidgets.mockResolvedValue([CHECKLIST]);
  listRoutines.mockResolvedValue({ routines: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useWidgets' state write", () => {
  it("shows the tick immediately, then settles on what the core stored", async () => {
    // The core answers with a DIFFERENT state than the one sent — which is what
    // it would do if it had clamped or normalised something. Whatever it says is
    // what the rail must end up showing.
    setWidgetState.mockResolvedValue({ ok: true, state: { checked: [false, true] } });
    const { hook } = setup();
    await act(async () => hook.result.current.refreshWidgets());

    act(() => hook.result.current.handleSetWidgetState("w-check", { checked: [true, false] }));
    // Optimistic: applied before the promise resolves.
    expect(hook.result.current.widgets[0].state).toEqual({ checked: [true, false] });

    await waitFor(() =>
      expect(hook.result.current.widgets[0].state).toEqual({ checked: [false, true] }),
    );
  });

  it("re-reads the rail when the core refuses, instead of keeping the tick", async () => {
    setWidgetState.mockResolvedValue({ ok: false, error: "That list has changed since." });
    const { hook, setStatusBanner } = setup();
    await act(async () => hook.result.current.refreshWidgets());

    await act(async () => {
      hook.result.current.handleSetWidgetState("w-check", { checked: [true, true] });
    });

    // Back to the core's version (listWidgets still answers with the untouched
    // widget), and the person is told why in the core's own words.
    await waitFor(() =>
      expect(hook.result.current.widgets[0].state).toEqual({ checked: [false, false] }),
    );
    expect(setStatusBanner).toHaveBeenCalledWith("That list has changed since.");
  });

  it("says something plain when the write never gets an answer at all", async () => {
    setWidgetState.mockRejectedValue(new Error("boom"));
    const { hook, setStatusBanner } = setup();
    await act(async () => hook.result.current.refreshWidgets());

    await act(async () => {
      hook.result.current.handleSetWidgetState("w-check", { checked: [true, true] });
    });

    await waitFor(() =>
      expect(setStatusBanner).toHaveBeenCalledWith("Couldn't save that change just now."),
    );
    // No stack trace, no raw error text — and the rail is re-read, not left on a
    // tick nothing acknowledged.
    await waitFor(() =>
      expect(hook.result.current.widgets[0].state).toEqual({ checked: [false, false] }),
    );
  });
});
