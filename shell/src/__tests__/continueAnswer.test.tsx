// "Continue this answer" — the truncation-aware second action beside Retry
// (KNOWN-GAPS, judged 2026-08-09; built 2026-08-22).
//
// It is deliberately NOT an always-present "make it longer" (design-doc §7.9.1
// keeps the command set short), so the whole design lives in when it does NOT
// appear. These tests pin the four gates — the core said this answer hit the
// model's output cap, it is the LAST answer, the thread is settled, and pressing
// it sends one fixed, visible sentence through the ordinary send path.

import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { ChatThread } from "../components/ChatThread";
import { CONTINUE_MESSAGE, useTurn } from "../hooks/useTurn";
import { ipc } from "../ipc/client";
import type { AnsweredWith, DisplayMessage } from "../types/ui";

vi.mock("../components/MermaidDiagram", () => ({ MermaidDiagram: () => null }));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// `globals: false` in vitest.config.ts means no automatic cleanup between tests;
// without this, every render stays in the document and "is it there?" stops
// meaning anything.
afterEach(() => {
  cleanup();
});

const LABEL = "Continue this answer";

function answered(over: Partial<AnsweredWith> = {}): AnsweredWith {
  return {
    modelId: "m-1",
    label: "A Model",
    free: false,
    routed: false,
    truncated: false,
    ...over,
  };
}

function renderThread(messages: DisplayMessage[], onContinue = vi.fn(), settled = true) {
  render(
    <ChatThread
      messages={messages}
      onRetry={vi.fn()}
      retryAvailable={settled}
      onContinue={onContinue}
      onRewindTo={vi.fn()}
    />,
  );
  return onContinue;
}

const CUT_OFF: DisplayMessage = {
  id: "a1",
  role: "assistant",
  content: "The first three reasons are",
  answeredWith: answered({ truncated: true }),
};

describe("when the offer to carry on appears", () => {
  it("appears on an answer the core said hit the model's output cap", () => {
    renderThread([{ id: "u1", role: "user", content: "list ten reasons" }, CUT_OFF]);
    expect(screen.getByText(LABEL)).toBeTruthy();
    // Retry is unchanged and still there — Continue is a SECOND action, never a
    // replacement for the one that is always available.
    expect(screen.getByText("Retry this answer")).toBeTruthy();
  });

  it("does not appear when the answer ended normally", () => {
    renderThread([
      { id: "a1", role: "assistant", content: "Done.", answeredWith: answered() },
    ]);
    expect(screen.queryByText(LABEL)).toBeNull();
    expect(screen.getByText("Retry this answer")).toBeTruthy();
  });

  it("does not appear when the reply carried no answeredWith block at all", () => {
    // The commonest shape in practice: an older core, or a turn that produced no
    // final answer. No claim is not a claim of truncation.
    renderThread([{ id: "a1", role: "assistant", content: "Done." }]);
    expect(screen.queryByText(LABEL)).toBeNull();
  });

  it("disappears from an older answer once a newer turn exists", () => {
    // Same rule as Retry: this is about the end of the conversation, not about a
    // row in it. Resuming a cut-off answer from three turns ago would resume the
    // wrong thing.
    renderThread([
      CUT_OFF,
      { id: "u2", role: "user", content: "something else" },
      { id: "a2", role: "assistant", content: "Sure.", answeredWith: answered() },
    ]);
    expect(screen.queryByText(LABEL)).toBeNull();
  });

  it("does not appear while a turn is still running", () => {
    // `retryAvailable` is App's "the thread is settled" fact; both actions wait
    // for it, so neither can be pressed into a turn already in flight.
    renderThread([CUT_OFF], vi.fn(), false);
    expect(screen.queryByText(LABEL)).toBeNull();
    expect(screen.queryByText("Retry this answer")).toBeNull();
  });

  it("does not survive onto a turn that failed", () => {
    // The adversarial case: a cut-off answer, then a Retry that errored. Retry
    // REPLACES the trailing answer with a fresh message, and a failed turn carries
    // no answeredWith at all — so the offer must be gone, not inherited from the
    // answer it replaced.
    renderThread([
      {
        id: "a2",
        role: "assistant",
        content: "Addison couldn't reach that model.",
        failed: true,
      },
    ]);
    expect(screen.queryByText(LABEL)).toBeNull();
  });

  it("does not appear on a turn the person stopped", () => {
    // Stopping is not the model running out of room, and the transcript should not
    // suggest otherwise.
    renderThread([{ id: "a1", role: "assistant", content: "(Stopped.)" }]);
    expect(screen.queryByText(LABEL)).toBeNull();
  });

  it("calls back exactly once when pressed", () => {
    const onContinue = renderThread([CUT_OFF]);
    fireEvent.click(screen.getByText(LABEL));
    expect(onContinue).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// What pressing it actually sends.
// ---------------------------------------------------------------------------

vi.mock("../ipc/client", () => ({
  ipc: { sendMessage: vi.fn(() => Promise.resolve({ ok: true })), stopTurn: vi.fn() },
  parseAnsweredWith: () => undefined,
}));

const sendMessage = ipc.sendMessage as unknown as ReturnType<typeof vi.fn>;

function turnArgs(connected = true) {
  return {
    connected,
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

describe("the message it sends", () => {
  beforeEach(() => {
    sendMessage.mockClear();
  });

  it("is one fixed, plain sentence, sent the ordinary way", async () => {
    const { result } = renderHook(() => useTurn(turnArgs()));
    await act(async () => {
      result.current.handleContinue();
    });

    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0][0]).toBe(CONTINUE_MESSAGE);
    // Plain language, no jargon, and it asks to RESUME rather than to start over
    // or to say more for its own sake.
    expect(CONTINUE_MESSAGE).toBe("Please carry on from where your last answer stopped.");
  });

  it("shows that sentence in the thread as an ordinary message from the person", async () => {
    // No hidden prompt: what was asked on someone's behalf is visible to them.
    const { result } = renderHook(() => useTurn(turnArgs()));
    await act(async () => {
      result.current.handleContinue();
    });

    const said = result.current.messages.filter((m) => m.role === "user");
    expect(said.map((m) => m.content)).toEqual([CONTINUE_MESSAGE]);
  });

  it("leaves the cut-off answer exactly where it is", async () => {
    // Continue is not Retry: the answer that was cut off is not wrong and is not
    // replaced. The result is two messages, never a spliced one.
    const { result } = renderHook(() => useTurn(turnArgs()));
    await act(async () => {
      result.current.handleSend("list ten reasons");
    });
    const before = result.current.messages.map((m) => `${m.role}:${m.content}`);

    await act(async () => {
      result.current.handleContinue();
    });

    const after = result.current.messages.map((m) => `${m.role}:${m.content}`);
    // Every row that existed is still there, unchanged and in order — Retry would
    // have dropped the trailing answer instead.
    expect(after.slice(0, before.length)).toEqual(before);
    expect(after.slice(before.length)).toEqual([`user:${CONTINUE_MESSAGE}`, "assistant:"]);
  });

  it("sends nothing at all when the engine is not connected", async () => {
    const { result } = renderHook(() => useTurn(turnArgs(false)));
    await act(async () => {
      result.current.handleContinue();
    });
    expect(sendMessage).not.toHaveBeenCalled();
  });
});
