// `model.startLocalSetup` waits as long as a turn does (KNOWN-BUGS 20, review).
//
// The core runs the call on its worker, so it queues behind a turn in progress.
// With the client's 120 s default, a long turn made the call give up while the
// core went on to start the download a moment later. The window then showed an
// error for a setup that was running. This drives the real `call` in
// ipc/client.ts with the Tauri bridge stubbed and the clock faked.

import { describe, it, expect, vi, afterEach } from "vitest";

afterEach(() => {
  vi.useRealTimers();
  delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  vi.doUnmock("@tauri-apps/api/core");
  vi.doUnmock("@tauri-apps/api/event");
  vi.resetModules();
});

describe("startLocalSetup's timeout", () => {
  it("outlasts the 120 s default and gives up only at the turn timeout", async () => {
    // Kills: calling it with the default timeout.
    vi.doMock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => undefined) }));
    vi.doMock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => {}) }));
    // The module reads `isEngineConnected()` per call, so the Tauri marker has to
    // be on `window` before the call.
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    vi.resetModules();
    vi.useFakeTimers();

    const { ipc } = await import("../ipc/client");
    const { invoke } = await import("@tauri-apps/api/core");

    let outcome: "waiting" | "resolved" | Error = "waiting";
    ipc.startLocalSetup("llama3.2:3b").then(
      () => {
        outcome = "resolved";
      },
      (error: Error) => {
        outcome = error;
      },
    );
    await vi.advanceTimersByTimeAsync(0);
    const frame = (vi.mocked(invoke).mock.calls[0]?.[1] as { frame: Record<string, unknown> })
      .frame;
    expect(frame.method).toBe("model.startLocalSetup");

    // Well past the default, as a long turn would take.
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000);
    expect(outcome).toBe("waiting");

    // The turn timeout is 900 s.
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000);
    expect(outcome).toBeInstanceOf(Error);
    expect((outcome as unknown as Error).message).toBe(
      "Addison took too long to answer. Please try again.",
    );
  });
});
