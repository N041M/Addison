// Windowed thread rendering (ChatThread.tsx). A long conversation still arrives
// whole from `loadConversation`; only a trailing window of it is RENDERED, which
// is what makes opening one cheap. Six properties, each one a regression guard
// with a named way to break it:
//
//   1. opening renders the trailing window only,
//   2. reaching the top reveals the previous batch,
//   3. the reader's scroll position survives that prepend,
//   4. a message appended mid-stream never evicts the top rendered row (the
//      window is anchored at the TOP — this is what forbids a `slice(-n)`),
//   5. a rewind that shortens the list past the window's edge still renders the
//      thread instead of a blank column (the clamp),
//   6. the conversation-switch stagger still animates the trailing rows — the
//      window may not starve the motion language it sits under.
//
// Plus two sanity checks in the same suite: a short conversation renders whole,
// and "Retry this answer" still lands on the last settled assistant row.
//
// The window sizes are mirrored here on purpose. They are module constants in
// the component (not settings, not props), so a change to either has to be made
// twice — and this suite goes red until it is.

import { describe, it, expect, vi, afterEach, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { ChatThread, resetThreadStaggerForTests } from "../components/ChatThread";
import type { DisplayMessage } from "../types/ui";

// globals:false → testing-library's automatic cleanup isn't registered.
afterEach(cleanup);

// jsdom doesn't implement scrollIntoView, which ChatThread calls after paint.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// The stagger key is module state in the component and survives unmounts by
// design (opening a chat from Settings remounts it) — so each case starts from
// a session that has never painted a thread.
beforeEach(() => {
  resetThreadStaggerForTests();
});

// Mirrors of ChatThread's module constants (see the header).
const INITIAL_WINDOW_ROWS = 30;
const EXTEND_BATCH_ROWS = 30;
const STAGGER_ROWS = 12;

const EMPTY_STATE_COPY = /Ask anything, or hand me a chore/;

/** `n` user turns whose bodies are greppable and free of markdown. */
function conversation(n: number): DisplayMessage[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `m-${i}`,
    role: "user" as const,
    content: `msg-${i}`,
    storeId: `s-${i}`,
  }));
}

function renderThread(messages: DisplayMessage[], conversationKey: string, retry = false) {
  return render(threadOf(messages, conversationKey, retry));
}

function threadOf(messages: DisplayMessage[], conversationKey: string, retry = false) {
  return (
    <ChatThread
      messages={messages}
      onRetry={vi.fn()}
      retryAvailable={retry}
      onRewindTo={vi.fn()}
      conversationKey={conversationKey}
    />
  );
}

/** The scroll container — the one carrying the fade mask. */
function listOf(container: HTMLElement): HTMLElement {
  const el = container.querySelector(".fade-mask-y");
  if (!el) throw new Error("no message list in the render");
  return el as HTMLElement;
}

/** How many message bodies are on screen (one text node per row). */
function renderedRowCount(): number {
  return screen.queryAllByText(/^msg-\d+$/).length;
}

/**
 * The row ELEMENTS, in order. Matched on the sender label rather than the body,
 * because a settled assistant body is markdown and carries no `data-msg-text` —
 * every row has a label, and the bottom-scroll sentinel has neither.
 */
function rowElements(list: HTMLElement): HTMLElement[] {
  return Array.from(list.children).filter((el) =>
    el.querySelector("[data-scramble-live]"),
  ) as HTMLElement[];
}

// ---------------------------------------------------------------------------
// 1. Only the trailing window renders on open.
// ---------------------------------------------------------------------------
describe("opening a long conversation", () => {
  it("renders only the trailing window", () => {
    renderThread(conversation(80), "c1");
    expect(screen.getByText("msg-79")).toBeTruthy();
    expect(screen.queryByText("msg-0")).toBeNull();
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS);
  });

  it("renders a short conversation whole", () => {
    renderThread(conversation(5), "short");
    expect(renderedRowCount()).toBe(5);
    expect(screen.getByText("msg-0")).toBeTruthy();
    expect(screen.getByText("msg-4")).toBeTruthy();
  });

  it("still offers Retry on the last settled assistant row of a windowed thread", () => {
    const messages = conversation(80);
    messages[79] = { id: "m-79", role: "assistant", content: "msg-79" };
    const { container } = renderThread(messages, "c1", true);
    const retry = screen.getByText("Retry this answer");
    // On the bottom row, which is the one it belongs to.
    expect(retry.closest(".group")).toBe(rowElements(listOf(container)).at(-1));
  });
});

// ---------------------------------------------------------------------------
// 2. Scrolling near the top reveals an earlier batch.
// ---------------------------------------------------------------------------
describe("extending the window upward", () => {
  it("reveals the previous batch when the reader reaches the top", () => {
    const { container } = renderThread(conversation(80), "c1");
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });

    // 80 messages, a 30-row window → 50 hidden; one batch releases 30 of them.
    expect(screen.queryByText("msg-20")).toBeNull();
    fireEvent.scroll(list);

    expect(screen.getByText("msg-20")).toBeTruthy();
    expect(screen.queryByText("msg-19")).toBeNull();
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + EXTEND_BATCH_ROWS);
  });

  // A window that doesn't overflow can never receive a scroll event, so the rows
  // above it would be unreachable. jsdom reports zero for both measurements,
  // which is why every other case in this file windows normally.
  it("extends itself when the window does not fill the container", () => {
    const { container, rerender } = render(threadOf(conversation(80), "c1"));
    const list = listOf(container);
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS); // jsdom: nothing measurable

    // A container taller than its own content — no overflow, so no scroll event
    // will ever arrive and the hidden rows have no other way back.
    Object.defineProperty(list, "clientHeight", { value: 600, configurable: true });
    Object.defineProperty(list, "scrollHeight", { value: 400, configurable: true });
    rerender(threadOf(conversation(80), "c1"));

    // Released a batch at a time until there is nothing left hidden.
    expect(renderedRowCount()).toBe(80);
  });

  // Being near the top is a STATE a gesture can sit in for many events; a batch
  // per event would render most of a long thread from one flick, which is the
  // cost the window exists to avoid.
  it("releases exactly one batch per arrival at the top", () => {
    const { container } = renderThread(conversation(400), "c1");
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });

    for (let i = 0; i < 8; i++) fireEvent.scroll(list);

    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + EXTEND_BATCH_ROWS);
  });

  it("does not extend on a scroll that is nowhere near the top", () => {
    const { container } = renderThread(conversation(80), "c1");
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 900, writable: true, configurable: true });

    fireEvent.scroll(list);

    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS);
  });

  it("arms again once the reader leaves the trigger zone", () => {
    const { container } = renderThread(conversation(400), "c1");
    const list = listOf(container);
    let top = 0;
    Object.defineProperty(list, "scrollTop", {
      configurable: true,
      get: () => top,
      set: (v: number) => {
        top = v;
      },
    });

    fireEvent.scroll(list); // arrival → one batch
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + EXTEND_BATCH_ROWS);

    top = 900; // scrolled away
    fireEvent.scroll(list);
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + EXTEND_BATCH_ROWS);

    top = 0; // and back to the top: a second ask
    fireEvent.scroll(list);
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + 2 * EXTEND_BATCH_ROWS);
  });

  // Scroll anchoring is the browser's own compensation for content growing
  // above the viewport — which this thread does whenever a diagram or an image
  // finishes rendering. It is suppressed for the prepend and must come back.
  it("does not leave scroll anchoring switched off after the prepend", () => {
    const { container } = renderThread(conversation(80), "c1");
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });

    fireEvent.scroll(list);

    expect(list.style.overflowAnchor).toBe("");
  });

  // An extension captured against the outgoing thread describes geometry that
  // means nothing in the new one. If it survives the switch, the restore applies
  // that delta to a different list and parks the reader at an offset the thread
  // has no room for.
  it("drops a pending extension when the conversation changes in the same batch", () => {
    const { container, rerender } = render(threadOf(conversation(80), "c1"));
    const list = listOf(container);
    let top = 0;
    Object.defineProperty(list, "scrollTop", {
      configurable: true,
      get: () => top,
      set: (v: number) => {
        top = v;
      },
    });
    // Modelled from what is rendered — 100px per row — so it does not matter
    // how many times the component reads it. A per-read script here was drained
    // by an unrelated read added later, which made this whole case vacuous.
    Object.defineProperty(list, "scrollHeight", {
      configurable: true,
      get: () => list.querySelectorAll(".group").length * 100,
    });

    act(() => {
      fireEvent.scroll(list); // captures the OLD thread's geometry
      rerender(threadOf(conversation(5), "c2")); // ...and the conversation changes
    });

    expect(top).toBe(0);
  });

  // ---------------------------------------------------------------------------
  // 3. The scroll position survives the prepend.
  // ---------------------------------------------------------------------------
  it("keeps the reader where they were when rows are prepended", () => {
    const { container } = renderThread(conversation(80), "c1");
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });
    // jsdom has no layout, so height is modelled from what is actually rendered
    // — 100px per row. Deliberately NOT a per-read script: the component reads
    // this property more than once per event, and a fixture that changes value
    // per read tests the reads rather than the geometry.
    Object.defineProperty(list, "scrollHeight", {
      configurable: true,
      get: () => list.querySelectorAll(".group").length * 100,
    });

    fireEvent.scroll(list); // 30 rows (3000px) → 60 rows (6000px)

    expect(list.scrollTop).toBe(3000);
  });
});

// ---------------------------------------------------------------------------
// The control above the window. Scrolling is for reading back; this is for
// searching, where find-in-page only ever sees what is rendered.
// ---------------------------------------------------------------------------
describe("the show-earlier control", () => {
  it("names how many messages are above the window, and reveals all of them", () => {
    renderThread(conversation(80), "c1");
    const button = screen.getByText("Show 50 earlier messages");

    fireEvent.click(button);

    expect(renderedRowCount()).toBe(80);
    expect(screen.getByText("msg-0")).toBeTruthy();
    expect(screen.queryByText(/earlier message/)).toBeNull(); // nothing left above
  });

  it("says it in the singular when one message is hidden", () => {
    renderThread(conversation(INITIAL_WINDOW_ROWS + 1), "c1");
    expect(screen.getByText("Show 1 earlier message")).toBeTruthy();
  });

  it("is absent when the whole conversation is on screen", () => {
    renderThread(conversation(5), "c1");
    expect(screen.queryByText(/earlier message/)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Following the newest message is a property of where the READER is. This
// effect runs on every App render and on every scramble frame of a streaming
// reply, so an unconditional scroll made reading back impossible mid-answer.
// ---------------------------------------------------------------------------
describe("following the bottom of the thread", () => {
  function scrollStateOn(list: HTMLElement, distanceFromBottom: number) {
    Object.defineProperty(list, "clientHeight", { value: 500, configurable: true });
    Object.defineProperty(list, "scrollHeight", { value: 5000, configurable: true });
    Object.defineProperty(list, "scrollTop", {
      value: 5000 - 500 - distanceFromBottom,
      writable: true,
      configurable: true,
    });
  }

  it("does not drag a reader who has scrolled up back to the bottom", () => {
    const { container, rerender } = render(threadOf(conversation(40), "c1"));
    const list = listOf(container);
    scrollStateOn(list, 2000); // deep in the history
    fireEvent.scroll(list);
    const calls = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length;

    // Addison writes another line.
    rerender(
      threadOf([...conversation(40), { id: "new", role: "assistant", content: "msg-40" }], "c1"),
    );

    expect(
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBe(calls);
  });

  it("keeps following while the reader is at the bottom", () => {
    const { container, rerender } = render(threadOf(conversation(40), "c1"));
    const list = listOf(container);
    scrollStateOn(list, 10); // still at the newest message
    fireEvent.scroll(list);
    const calls = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length;

    rerender(
      threadOf([...conversation(40), { id: "new", role: "assistant", content: "msg-40" }], "c1"),
    );

    expect(
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(calls);
  });

  // The shape `runTurn` ACTUALLY produces: the user's row and the pending
  // assistant row arrive in ONE update (useTurn.ts), so the newest message is
  // the assistant's. An earlier version of this test appended a bare user row —
  // a state the product never reaches — and passed against code whose force
  // could never fire on a real send.
  it("comes back down when the person sends, as runTurn builds it", () => {
    const { container, rerender } = render(threadOf(conversation(40), "c1"));
    const list = listOf(container);
    scrollStateOn(list, 2000);
    fireEvent.scroll(list);
    const calls = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length;

    rerender(
      threadOf(
        [
          ...conversation(40),
          { id: "u-new", role: "user", content: "msg-40" },
          { id: "a-new", role: "assistant", content: "", pending: true },
        ],
        "c1",
      ),
    );

    expect(
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(calls);
  });

  // A rewind SHORTENS the list, which can make an older user row the newest one
  // — that is not a send, and must not drag the reader anywhere.
  it("stays put when a rewind makes an older message the newest", () => {
    const { container, rerender } = render(
      threadOf([...conversation(40), { id: "u-x", role: "user", content: "msg-40" }], "c1"),
    );
    const list = listOf(container);
    scrollStateOn(list, 2000);
    fireEvent.scroll(list);
    const calls = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length;

    rerender(threadOf(conversation(35), "c1")); // rewound: shorter, new newest id

    expect((Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length).toBe(
      calls,
    );
  });

  it("comes back down for a retry, which appends no user message at all", () => {
    const messages = conversation(40);
    messages[39] = { id: "m-39", role: "assistant", content: "msg-39" };
    const { container, rerender } = render(threadOf(messages, "c1", true));
    const list = listOf(container);
    scrollStateOn(list, 2000);
    fireEvent.scroll(list);
    const calls = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length;

    fireEvent.click(screen.getByText("Retry this answer"));
    // runTurn drops the trailing assistant and appends a fresh pending one.
    // `rerender`, never a second `render`: a fresh mount starts out following
    // the bottom, so it would scroll whether or not the retry said to.
    const retried = [
      ...messages.slice(0, 39),
      { id: "a-2", role: "assistant" as const, content: "", pending: true },
    ];
    rerender(threadOf(retried, "c1", true));

    expect(
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(calls);
  });
});

// ---------------------------------------------------------------------------
// A Settings detour unmounts ChatThread; a rewind shortens the list. Neither
// may quietly take back rows the reader had opened out.
// ---------------------------------------------------------------------------
describe("a window the reader opened out", () => {
  it("survives leaving the chat column and coming back", () => {
    const first = render(threadOf(conversation(80), "c1"));
    const list = listOf(first.container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });
    fireEvent.scroll(list);
    expect(renderedRowCount()).toBe(60);

    first.unmount(); // a surface replaces the chat column
    render(threadOf(conversation(80), "c1"));

    expect(renderedRowCount()).toBe(60);
  });

  it("is not collapsed by a rewind that shortens the thread", () => {
    const { container, rerender } = render(threadOf(conversation(80), "c1"));
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });
    fireEvent.scroll(list);
    expect(renderedRowCount()).toBe(60); // showing msg-20..79

    rerender(threadOf(conversation(50), "c1")); // rewound

    expect(renderedRowCount()).toBe(50); // everything that is left, not 30
    expect(screen.getByText("msg-0")).toBeTruthy();
  });

  // The switch resets. Both lines below are load-bearing and both survived the
  // suite until these cases existed.
  it("follows the newest message again after switching conversations", () => {
    // Assistant-terminated: a new user row would force the follow on its own and
    // hide a broken reset.
    const ends = (key: string) =>
      [...conversation(40), { id: `${key}-a`, role: "assistant" as const, content: `${key}-tail` }];
    const { container, rerender } = render(threadOf(ends("c1"), "c1"));
    const list = listOf(container);
    Object.defineProperty(list, "clientHeight", { value: 500, configurable: true });
    Object.defineProperty(list, "scrollHeight", { value: 5000, configurable: true });
    Object.defineProperty(list, "scrollTop", { value: 500, writable: true, configurable: true });
    fireEvent.scroll(list); // deep in the history, no longer following

    rerender(threadOf(ends("c2"), "c2"));
    const calls = (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length;
    rerender(threadOf([...ends("c2"), { id: "c2-b", role: "assistant", content: "later" }], "c2"));

    expect(
      (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.length,
    ).toBeGreaterThan(calls);
  });

  it("can extend the new conversation immediately after a switch", () => {
    const { container, rerender } = render(threadOf(conversation(80), "c1"));
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });
    fireEvent.scroll(list); // spends c1's arming

    rerender(threadOf(conversation(80), "c2"));
    fireEvent.scroll(list); // still at the top, now in c2

    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + EXTEND_BATCH_ROWS);
  });

  // The floor is what the reader ASKED to see, not the size the DOM reached. A
  // thread left open grows on its own; treating that as the floor would render
  // every one of those rows again on the next rewind.
  it("does not re-render a whole long-lived thread on a rewind", () => {
    const { rerender } = render(threadOf(conversation(80), "c1")); // 30 shown
    rerender(threadOf(conversation(200), "c1")); // grew while open → 150 shown

    rerender(threadOf(conversation(100), "c1")); // rewound

    expect(renderedRowCount()).toBe(50); // the 50 that were never hidden, not 100
  });

  // The remembered window and the live floor have to agree, or a detour quietly
  // hands back fewer rows than the reader had opened out.
  it("gives back the same window after a Settings detour as without one", () => {
    const first = render(threadOf(conversation(80), "c1"));
    const list = listOf(first.container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });
    fireEvent.scroll(list);
    expect(renderedRowCount()).toBe(60);

    first.unmount();
    const second = render(threadOf(conversation(80), "c1"));
    second.rerender(threadOf(conversation(40), "c1")); // rewind after coming back

    expect(renderedRowCount()).toBe(40); // everything left, exactly as if never unmounted
  });

  // Rows that accumulated at the bottom while the thread sat open were never
  // asked for, so coming back should look like opening the conversation.
  it("remembers what the reader asked for, not what the thread grew to", () => {
    const first = render(threadOf(conversation(80), "c1"));
    first.rerender(threadOf(conversation(200), "c1")); // 150 rows, all unasked
    first.unmount();

    render(threadOf(conversation(200), "c1"));

    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS);
  });

  it("does not carry one conversation's window over to another", () => {
    const { container, rerender } = render(threadOf(conversation(80), "c1"));
    const list = listOf(container);
    Object.defineProperty(list, "scrollTop", { value: 0, writable: true, configurable: true });
    fireEvent.scroll(list);
    expect(renderedRowCount()).toBe(60);

    rerender(threadOf(conversation(80), "c2"));

    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS);
  });
});

// ---------------------------------------------------------------------------
// 4. An appended message grows the window at the bottom, never at the top's
//    expense. This is the test that forbids a `slice(-n)` rewrite.
// ---------------------------------------------------------------------------
describe("a message arriving while the thread is open", () => {
  it("never evicts the top rendered row", () => {
    const messages = conversation(80);
    const { container, rerender } = render(threadOf(messages, "c1"));
    const topRow = rowElements(listOf(container))[0];
    expect(topRow.textContent).toContain("msg-50");

    rerender(threadOf(conversation(81), "c1"));

    expect(screen.getByText("msg-50")).toBeTruthy();
    expect(screen.getByText("msg-80")).toBeTruthy();
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + 1);
  });
});

// ---------------------------------------------------------------------------
// 5. A rewind can shorten the list to exactly the window's top edge (App's
//    handleRewindTo does `prev.slice(0, idx)` under the same key).
// ---------------------------------------------------------------------------
describe("rewinding past the window's edge", () => {
  it("still renders the thread instead of a blank column", () => {
    const { rerender } = render(threadOf(conversation(80), "c1"));

    // Rewound to the topmost rendered row: 50 messages left, 50 were hidden.
    rerender(threadOf(conversation(50), "c1"));

    expect(screen.queryByText(EMPTY_STATE_COPY)).toBeNull();
    expect(screen.getByText("msg-49")).toBeTruthy();
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS);
  });

  // The narrowed count has to be WRITTEN BACK, not merely derived for the one
  // render that needs it. A stale count left in state is a ceiling the window
  // climbs back to as the shortened thread grows again — re-hiding rows the
  // reader can already see, which is the eviction the top anchor exists to
  // forbid (property 4, in the state the rewind leaves behind).
  it("does not re-hide rows when the shortened thread grows again", () => {
    const { container, rerender } = render(threadOf(conversation(80), "c1"));
    rerender(threadOf(conversation(50), "c1"));
    const topRow = rowElements(listOf(container))[0];
    expect(topRow.textContent).toContain("msg-20");

    // The message the person sends after rewinding.
    rerender(threadOf(conversation(51), "c1"));

    expect(screen.getByText("msg-20")).toBeTruthy();
    expect(screen.getByText("msg-50")).toBeTruthy();
    expect(renderedRowCount()).toBe(INITIAL_WINDOW_ROWS + 1);
  });
});

// ---------------------------------------------------------------------------
// 6. The switch stagger still animates the trailing rows of the window.
// ---------------------------------------------------------------------------
describe("the conversation-switch stagger", () => {
  it("still animates the trailing rows once the thread is windowed", () => {
    // The stagger scrambles the row labels, which schedules real timers; fake
    // them HERE only, so nothing outlives this case (and no other case pays for
    // a global clock).
    vi.useFakeTimers();
    try {
      const { container, rerender } = render(threadOf(conversation(80), "c1"));
      rerender(threadOf(conversation(80), "c2"));

      const rows = rowElements(listOf(container));
      // The effect animates the last STAGGER_ROWS CHILDREN, one of which is the
      // bottom-scroll sentinel — so eleven rows move, which is the behaviour
      // that shipped before windowing and is unchanged by it.
      for (const row of rows.slice(-(STAGGER_ROWS - 1))) {
        expect(row.style.animation).toContain("fadeRise");
      }
      const above = rows[rows.length - STAGGER_ROWS];
      expect(above).toBeTruthy();
      expect(above.style.animation).toBe("");
    } finally {
      vi.clearAllTimers();
      vi.useRealTimers();
    }
  });
});
