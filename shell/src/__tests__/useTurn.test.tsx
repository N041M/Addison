// useTurn's currentTurnRef race guard (maintainability review 2026-07-19, item
// 5). The v1 IPC contract has NO core-side cancel, so a turn's result can still
// land after the user hit Stop or after a newer turn superseded it. runTurn
// stamps each turn with an id in currentTurnRef and drops any result whose id no
// longer matches (the guards at useTurn.ts ~93 / ~117 / ~142). These tests pin
// that behavior: a late result must never resurrect stopped text or clobber a
// newer turn's answer, and must not re-enable the composer.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useTurn } from "../hooks/useTurn";
import { ipc } from "../ipc/client";
import { setMotionEnabled } from "../lib/scramble";

// The hook only touches ipc.sendMessage on the tested paths; mock the whole
// module so no Tauri context is needed (RawError is a type — erased at build).
// `parseAnsweredWith` is also imported by the hook (Phase-2 step 3) — stub it to
// the fail-closed default (no chip) so these race-guard tests stay focused.
vi.mock("../ipc/client", () => ({
  ipc: { sendMessage: vi.fn() },
  parseAnsweredWith: () => undefined,
}));

const sendMessage = ipc.sendMessage as unknown as ReturnType<typeof vi.fn>;

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (err: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// Each sendMessage call hands back the next queued deferred, so the test drives
// exactly when (and in which order) turn A vs turn B resolves.
let deferreds: Array<Deferred<unknown>>;

function makeArgs() {
  return {
    connected: true,
    setStatusBanner: vi.fn(),
    selectedRole: "primary" as const,
    selectedLocalModel: undefined,
    selectedEffort: undefined,
    effectiveLocalModel: vi.fn(() => undefined),
    effectiveCloudModel: vi.fn(() => "claude-opus-4-8"),
    maybeProposeWidget: vi.fn(),
    maybeProposeOffers: vi.fn(),
    refreshConversations: vi.fn(),
    refreshStats: vi.fn(),
  };
}

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  deferreds = [];
  sendMessage.mockReset();
  sendMessage.mockImplementation(() => {
    const d = deferred<unknown>();
    deferreds.push(d);
    return d.promise;
  });
});

// The dark redesign retired the seeded "welcome" assistant line: an untouched
// chat renders ChatThread's greeting stack, which is only reachable when the
// thread is genuinely empty (ChatThread's `isEmpty`). App hands `turn.messages`
// straight through with no filtering now, so a seed reintroduced here would put
// a message Addison never sent on screen and suppress the stack. This is the
// test that says the thread starts empty.
describe("a new thread", () => {
  it("starts with no messages, so the empty state can show", () => {
    const { result } = renderHook(() => useTurn(makeArgs()));
    expect(result.current.messages).toEqual([]);
  });
});

describe("useTurn race guard", () => {
  it("drops a result that arrives after Stop", async () => {
    const args = makeArgs();
    const { result } = renderHook(() => useTurn(args));

    act(() => {
      result.current.handleSend("A");
    });
    expect(result.current.isWorking).toBe(true);
    // A pending assistant bubble was appended.
    expect(result.current.messages.at(-1)).toMatchObject({ role: "assistant", pending: true });

    act(() => {
      result.current.handleStop();
    });
    expect(result.current.isWorking).toBe(false);
    expect(result.current.messages.at(-1)).toMatchObject({ content: "(Stopped.)", pending: false });

    // A's result lands late — the guard must discard it.
    await act(async () => {
      deferreds[0].resolve({ text: "A answer" });
      await flushMicrotasks();
    });

    expect(result.current.messages.some((m) => m.content === "A answer")).toBe(false);
    expect(result.current.messages.at(-1)).toMatchObject({ content: "(Stopped.)", pending: false });
    expect(result.current.isWorking).toBe(false);
    // The dropped turn's `finally` guard also skips the post-turn refreshers.
    expect(args.refreshStats).not.toHaveBeenCalled();
    expect(args.maybeProposeWidget).not.toHaveBeenCalled();
  });

  it("drops a superseded turn's result and keeps the newer turn's answer", async () => {
    const args = makeArgs();
    const { result } = renderHook(() => useTurn(args));

    act(() => {
      result.current.handleSend("A");
    });
    const turnAAssistantId = result.current.messages.at(-1)!.id;

    // Start B before A resolves — currentTurnRef now points at B.
    act(() => {
      result.current.handleSend("B");
    });

    // A resolves late: dropped, must not touch A's still-pending bubble.
    await act(async () => {
      deferreds[0].resolve({ text: "A answer" });
      await flushMicrotasks();
    });
    expect(result.current.messages.some((m) => m.content === "A answer")).toBe(false);
    const staleA = result.current.messages.find((m) => m.id === turnAAssistantId)!;
    expect(staleA).toMatchObject({ content: "", pending: true });

    // B resolves: applied normally.
    await act(async () => {
      deferreds[1].resolve({ text: "B answer" });
      await flushMicrotasks();
    });
    expect(result.current.messages.some((m) => m.content === "B answer")).toBe(true);
    expect(result.current.isWorking).toBe(false);
    // Only the winning turn ran its post-turn side effects, once, for "B".
    expect(args.maybeProposeWidget).toHaveBeenCalledTimes(1);
    expect(args.maybeProposeWidget).toHaveBeenCalledWith("B");
    expect(args.refreshStats).toHaveBeenCalledTimes(1);
    // The offers drafter rides the same post-turn path, on the same text.
    expect(args.maybeProposeOffers).toHaveBeenCalledTimes(1);
    expect(args.maybeProposeOffers).toHaveBeenCalledWith("B");
  });

  // The answer is already on screen when the post-turn drafters run, so a drafter
  // that throws must not reach the failure path: `content || message` would keep
  // the text but stamp the turn `failed: true` — telling the person their answer
  // went wrong when it did not. Revert the inner try/catch in runTurn and this
  // goes red on `failed`.
  it("a throwing post-turn drafter does not mark a good turn as failed", async () => {
    const args = makeArgs();
    args.maybeProposeOffers = vi.fn(() => {
      throw new Error("drafting blew up");
    });
    const { result } = renderHook(() => useTurn(args));

    act(() => {
      result.current.handleSend("cheaper please");
    });
    await act(async () => {
      deferreds[0].resolve({ text: "the answer" });
      await flushMicrotasks();
    });

    const assistant = result.current.messages.at(-1)!;
    expect(assistant.content).toBe("the answer");
    expect(assistant.failed).toBeFalsy();
    expect(assistant.pending).toBe(false);
    expect(result.current.isWorking).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Streamed text: the scramble is a DECORATION, and the store never sees it.
//
// The streaming animation writes random glyphs into the tail of an arriving
// answer. If those glyphs could reach the message content, they would reach
// everything downstream of it: Retry re-sends the last user text but a rewind
// re-seeds the composer from message content, and the thread's own copy is what
// a person reads back later. A motion flourish that can put "#%&*" inside a
// sentence Addison wrote is not a flourish. So the true text and the displayed
// text are two different values, and this is the test that says so.
// ---------------------------------------------------------------------------
describe("streamed text vs. the streaming scramble", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  });
  afterEach(() => {
    vi.useRealTimers();
    setMotionEnabled(true);
  });

  const CHUNKS = ["Happy to help — ", "here is where I landed ", "after a first look."];
  const TRUE_TEXT = CHUNKS.join("");

  function sendAndStream() {
    const args = makeArgs();
    const rendered = renderHook(() => useTurn(args));
    act(() => {
      rendered.result.current.handleSend("have a look");
    });
    act(() => {
      for (const chunk of CHUNKS) rendered.result.current.appendStreamedText(chunk);
    });
    return { args, ...rendered };
  }

  it("commits the true text to state while the display is still scrambled", () => {
    const { result } = sendAndStream();

    act(() => {
      vi.advanceTimersByTime(38 * 2); // mid-flight: the window is over the tail
    });

    // What is COMMITTED is the model's text, byte for byte...
    expect(result.current.messages.at(-1)).toMatchObject({
      pending: true,
      content: TRUE_TEXT,
    });
    // ...and what is DISPLAYED is two ticks' worth of window (5 chars each),
    // whose contents are still noise rather than the answer's opening words.
    const display = result.current.streamDisplay!;
    expect(display).toHaveLength(10);
    expect(display).not.toBe(TRUE_TEXT.slice(0, 10));
  });

  it("drops the overlay when the turn settles, so the real answer shows", async () => {
    const { result } = sendAndStream();

    await act(async () => {
      deferreds[0].resolve({ text: TRUE_TEXT });
      await flushMicrotasks();
    });

    expect(result.current.streamDisplay).toBeNull();
    expect(result.current.messages.at(-1)).toMatchObject({
      pending: false,
      content: TRUE_TEXT,
    });
  });

  it("drops the overlay on Stop, and keeps what actually arrived", () => {
    const { result } = sendAndStream();

    act(() => {
      vi.advanceTimersByTime(38);
      result.current.handleStop();
    });

    expect(result.current.streamDisplay).toBeNull();
    expect(result.current.messages.at(-1)).toMatchObject({
      pending: false,
      content: TRUE_TEXT,
    });
  });

  it("adds no overlay at all with motion off — the text just appends", () => {
    setMotionEnabled(false);
    const { result } = sendAndStream();

    act(() => {
      vi.advanceTimersByTime(38 * 40);
    });

    expect(result.current.streamDisplay).toBeNull();
    expect(result.current.messages.at(-1)).toMatchObject({ content: TRUE_TEXT });
  });
});
