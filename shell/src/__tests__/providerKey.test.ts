// The store boundary's refusal has to REACH the person (secrets-and-keychain
// plan §5.3).
//
// Rust normalises a key where it is stored and refuses the shapes it cannot
// repair without guessing, each with its own plain sentence. That sentence is
// only worth writing if it survives the trip back: a Tauri command returning
// `Err(String)` rejects with the BARE STRING, and the Settings provider row's
// catch is `err instanceof Error ? err.message : "Couldn't connect. Check the
// key and try again."` — so an unwrapped rejection is silently replaced by the
// generic sentence, and the person is told to check a key that is not wrong.

import { describe, expect, it, vi } from "vitest";

const LINE_BREAK = "That key has a line break in it — paste it again as one line.";

async function saveAndCatch(rejection: unknown): Promise<unknown> {
  vi.doMock("@tauri-apps/api/core", () => ({
    invoke: vi.fn(async () => {
      throw rejection;
    }),
  }));
  vi.doMock("@tauri-apps/api/event", () => ({
    listen: vi.fn(async () => () => {}),
  }));
  // `isEngineConnected()` is read per call, so the Tauri marker has to be on
  // `window` before the call, not merely before the import.
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  vi.resetModules();
  try {
    const { storeProviderKey } = await import("../ipc/client");
    return await storeProviderKey("anthropic", "sk-a\nb").then(
      () => null,
      (err: unknown) => err,
    );
  } finally {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    vi.doUnmock("@tauri-apps/api/core");
    vi.doUnmock("@tauri-apps/api/event");
    vi.resetModules();
  }
}

describe("storeProviderKey — the §5.3 refusal survives the wire", () => {
  it("rejects with a real Error carrying the store boundary's own sentence", async () => {
    const failure = await saveAndCatch(LINE_BREAK);
    // An Error, because that is the only shape the Settings row will re-show.
    expect(failure).toBeInstanceOf(Error);
    // And the sentence itself, unaltered — it names what to do about the paste,
    // which no generic fallback can.
    expect((failure as Error).message).toBe(LINE_BREAK);
  });

  it("still says something plain when the shell rejects with nothing readable", async () => {
    const failure = await saveAndCatch(undefined);
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe("Something went wrong talking to Addison.");
  });
});
