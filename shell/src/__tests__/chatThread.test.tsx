// The one rendering rule the whole-answer reveal depends on (owner request
// 2026-07-26): while a message's text is resolving out of the scramble, its
// body renders as PLAIN pre-wrap text — markdown only takes over on the frame
// the reveal lands.
//
// This is not a style preference. The scramble writes random glyphs into the
// text 26 times a second, and markdown is structural: a stray `#` at the start
// of a frame becomes an <h1> for 38ms, a stray `-` becomes a list item, and the
// answer visibly reflows under the reader's eyes while they are trying to read
// it. The guard is one `&& !revealing` in ChatThread, and it was proven
// necessary the hard way — with the guard reverted, the entire 235-test suite
// still passed, so nothing but this file is watching it.
//
// Mermaid is stubbed: it is irrelevant here and pulls a heavy async renderer
// into jsdom. Nothing else is mocked — the real Markdown component is what has
// to be kept away from a half-scrambled string.

import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { ChatThread, resetThreadStaggerForTests } from "../components/ChatThread";
import { setMotionEnabled } from "../lib/scramble";
import type { DisplayMessage } from "../types/ui";

vi.mock("../components/MermaidDiagram", () => ({
  MermaidDiagram: () => null,
}));

// jsdom implements no layout, so it ships no scrollIntoView; the thread's
// keep-the-newest-line-in-view effect calls it. A spy rather than a stub, since
// how OFTEN it is called is itself a property under test below.
const scrollIntoView = vi.fn();
beforeAll(() => {
  Element.prototype.scrollIntoView = scrollIntoView;
});

// The stagger's "which conversation did we last animate" key is MODULE scope (so
// a remount can't swallow a real switch), which makes these tests order-
// dependent unless each starts from a blank session.
beforeEach(() => {
  scrollIntoView.mockClear();
  resetThreadStaggerForTests();
});

afterEach(() => {
  setMotionEnabled(true);
});

const ANSWER = "# Tidied\n\nI moved **24** files.";
const MESSAGE: DisplayMessage = {
  id: "a1",
  role: "assistant",
  content: ANSWER,
  pending: false,
};

function renderThread(extra: Partial<React.ComponentProps<typeof ChatThread>>) {
  return render(
    <ChatThread
      messages={[MESSAGE]}
      onRetry={() => {}}
      retryAvailable={false}
      onRewindTo={() => {}}
      {...extra}
    />,
  );
}

describe("a message whose text is still resolving", () => {
  it("renders plain text, never markdown, while the scramble is over it", () => {
    // A frame mid-reveal: the leading `#` is present, as it is in the real text.
    const frame = "# Tid%&d\n\nI m*ved **24** f#les.";
    const { container } = renderThread({
      streamMessageId: "a1",
      streamDisplay: frame,
    });

    // The heading must NOT have been parsed — no markdown structure at all.
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("strong")).toBeNull();
    // What is on screen is the frame itself, verbatim — asserted on the node's
    // own textContent, since a text matcher would normalise the newlines away
    // and this string's line breaks are part of what must survive.
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe(frame);
  });

  it("hands the message back to markdown once the reveal is over", () => {
    const { container } = renderThread({ streamMessageId: null, streamDisplay: null });

    expect(container.querySelector("h1")?.textContent).toBe("Tidied");
    expect(container.querySelector("strong")?.textContent).toBe("24");
  });

  it("leaves other messages alone while one of them is revealing", () => {
    const other: DisplayMessage = {
      id: "a0",
      role: "assistant",
      content: "# Earlier\n\nDone.",
      pending: false,
    };
    const { container } = render(
      <ChatThread
        messages={[other, MESSAGE]}
        onRetry={() => {}}
        retryAvailable={false}
        onRewindTo={() => {}}
        streamMessageId="a1"
        streamDisplay="# Tid%&d"
      />,
    );

    // The earlier answer keeps its markdown — the overlay is keyed to ONE id,
    // so a reveal can never flatten the rest of the thread.
    expect(container.querySelector("h1")?.textContent).toBe("Earlier");
  });
});

// ---------------------------------------------------------------------------
// The two rendering flags that decide what a row SAYS. Neither was covered:
// dropping either one is invisible to a screenshot and changes what the person
// reads.
// ---------------------------------------------------------------------------
describe("what a row says", () => {
  const FAILED: DisplayMessage = {
    id: "a2",
    role: "assistant",
    content: "I couldn't reach the model. Check the connection and try again.",
    failed: true,
    raw: "httpx.ConnectError: [Errno 61] Connection refused\n  at provider.send()",
  };

  // CLAUDE.md: no stack traces reach the user. The raw exception text is
  // Developer-profile diagnostics; in Simple the plain sentence is the whole of
  // it. No test passed this flag at all, so dropping it from the condition —
  // leaking a traceback into the companion's thread — kept the suite green.
  it("keeps the raw exception out of the thread unless the flag is on", () => {
    const { container } = renderThread({ messages: [FAILED] });

    expect(container.querySelector("details")).toBeNull();
    expect(container.textContent).not.toContain("ConnectError");
    // The plain-language sentence is what's on screen.
    expect(container.textContent).toContain("I couldn't reach the model.");
  });

  it("shows it in a collapsed block for the Developer profile", () => {
    const { container } = renderThread({ messages: [FAILED], showTechnicalDetails: true });

    expect(container.querySelector("summary")?.textContent).toBe("Technical details");
    expect(container.querySelector("pre")?.textContent).toBe(FAILED.raw);
  });

  // "Addison is writing…" is the copy for a turn with NOTHING to show yet. Once
  // a frame exists it is the frame's turn — dropping the emptiness check
  // replaces every streamed frame with the placeholder, so a reply that is
  // visibly arriving reads as one that hasn't started.
  it("shows the arriving frame, not the placeholder, once there is one", () => {
    const pending: DisplayMessage = { id: "p1", role: "assistant", content: "", pending: true };

    const empty = renderThread({ messages: [pending] });
    expect(empty.container.querySelector("[data-msg-text]")?.textContent).toBe(
      "Addison is writing…",
    );
    empty.unmount();

    const frame = renderThread({
      messages: [pending],
      streamMessageId: "p1",
      streamDisplay: "Hap%& t&",
    });
    expect(frame.container.querySelector("[data-msg-text]")?.textContent).toBe("Hap%& t&");
  });
});

// ---------------------------------------------------------------------------
// Auto-scroll. The thread follows the newest content — but a reveal repaints ~30
// times in 1.1s, and every one of those used to force a `scrollIntoView`, so
// scrolling up to reread an earlier answer was impossible: the thread yanked
// itself back down before the wheel stopped. App also rebuilds the `footer` as a
// fresh inline fragment on every render, so listing it as a dependency scrolled
// the thread on state changes elsewhere in the app.
// ---------------------------------------------------------------------------
describe("keeping the newest content in view", () => {
  const ROWS = [
    { id: "u1", role: "user", content: "Tidy my Downloads folder" } as DisplayMessage,
    MESSAGE,
  ];

  function list(container: HTMLElement): HTMLElement {
    return container.firstElementChild as HTMLElement;
  }

  /** jsdom has no layout, so the geometry the handler reads is supplied here. */
  function scrollTo(el: HTMLElement, distanceFromBottom: number) {
    Object.defineProperty(el, "scrollHeight", { value: 2000, configurable: true });
    Object.defineProperty(el, "clientHeight", { value: 500, configurable: true });
    Object.defineProperty(el, "scrollTop", {
      value: 2000 - 500 - distanceFromBottom,
      configurable: true,
    });
    fireEvent.scroll(el);
  }

  it("scrolls a new message into view", () => {
    const props = {
      messages: ROWS,
      onRetry() {},
      retryAvailable: false,
      onRewindTo() {},
    };
    const { rerender } = render(<ChatThread {...props} />);
    scrollIntoView.mockClear();

    rerender(<ChatThread {...props} messages={[...ROWS, { ...MESSAGE, id: "a2" }]} />);
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("does not scroll on a re-render that brought no new content", () => {
    const props = {
      messages: ROWS,
      onRetry() {},
      retryAvailable: false,
      onRewindTo() {},
    };
    const { rerender } = render(<ChatThread {...props} footer={<div>one</div>} />);
    scrollIntoView.mockClear();

    // Same messages, a new footer element — exactly what App hands over on any
    // unrelated state change, since its footer is an inline fragment.
    rerender(<ChatThread {...props} footer={<div>two</div>} />);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("leaves a reader who has scrolled up where they are, and follows again when they return", () => {
    const props = {
      messages: ROWS,
      onRetry() {},
      retryAvailable: false,
      onRewindTo() {},
      streamMessageId: "a1",
    };
    const { container, rerender } = render(<ChatThread {...props} streamDisplay="# Ti" />);

    scrollTo(list(container), 600); // scrolled up to reread
    scrollIntoView.mockClear();
    rerender(<ChatThread {...props} streamDisplay="# Tid%&d" />);
    expect(scrollIntoView).not.toHaveBeenCalled();

    scrollTo(list(container), 4); // back at the foot of the thread
    rerender(<ChatThread {...props} streamDisplay="# Tid%&d\n\nI m" />);
    expect(scrollIntoView).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Opening another conversation: the visible rows rise in one after another and
// their labels/bodies resolve out of the scramble. Four separate mutations of
// this effect survived the whole suite before these existed — it had no
// coverage at all.
// ---------------------------------------------------------------------------
describe("the conversation-switch stagger", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  function userRows(count: number): DisplayMessage[] {
    return Array.from({ length: count }, (_, i) => ({
      id: `u${i}`,
      role: "user" as const,
      content: `Message number ${i}`,
    }));
  }

  function open(messages: DisplayMessage[], conversationKey: string | null) {
    const props = { messages, onRetry() {}, retryAvailable: false, onRewindTo() {}, conversationKey };
    const view = render(<ChatThread {...props} />);
    return {
      ...view,
      switchTo(nextKey: string | null, nextMessages = messages) {
        view.rerender(<ChatThread {...props} conversationKey={nextKey} messages={nextMessages} />);
      },
      // The list's element children: one per visible row, plus the bottom
      // sentinel div the scroll effect targets.
      rows: () => Array.from(view.container.firstElementChild!.children) as HTMLElement[],
    };
  }

  // The session's first paint belongs to App's initial scramble pass; playing
  // the switch on top of it runs two loops over the same text nodes.
  it("plays nothing on the session's first paint", () => {
    const { rows } = open(userRows(3), "c-1");

    expect(rows().every((el) => el.style.animation === "")).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("staggers the rows when another conversation is opened", () => {
    const view = open(userRows(3), "c-1");

    view.switchTo("c-2", userRows(3));

    const rows = view.rows();
    expect(rows[0].style.animation).toContain("fadeRise");
    // Each row's own delay, 70ms apart, so they arrive one after another.
    expect(rows[1].style.animation).toContain("70ms");
    expect(vi.getTimerCount()).toBeGreaterThan(0); // …and the text is resolving
  });

  // A real conversation can hold hundreds of turns. Animating every row means a
  // forced layout and a 38ms scramble timer PER ROW, which was a measurable part
  // of "some chats take longer to open than others" — and the rows above the
  // fold are not on screen to be seen either way.
  it("animates only the trailing rows a bottom-scrolled viewport can show", () => {
    const view = open(userRows(30), "c-1");

    view.switchTo("c-2", userRows(30));

    const rows = view.rows();
    expect(rows).toHaveLength(31); // 30 messages + the bottom sentinel
    expect(rows[0].style.animation).toBe("");
    expect(rows[17].style.animation).toBe("");
    // The last twelve children, and no more.
    expect(rows[19].style.animation).toContain("fadeRise");
    expect(rows[30].style.animation).toContain("fadeRise");
  });

  // ChatThread unmounts whenever a surface replaces the chat column, which is
  // why the "already staggered" key is module scope: an instance ref forgot
  // across the round trip, so opening a chat FROM Settings remounted a fresh
  // component and re-animated a conversation that never changed.
  it("does not replay the stagger when the thread remounts on the same conversation", () => {
    const first = open(userRows(3), "c-1");
    first.switchTo("c-2");
    first.unmount();

    const again = open(userRows(3), "c-2");

    expect(again.rows().every((el) => el.style.animation === "")).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  // Learning the launch conversation's id is not a switch. `null` means "the
  // conversation the core minted for this launch"; the first completed turn
  // adopts it, so the key goes null → "c-…" with the same messages on screen —
  // and the thread re-staggered and re-scrambled itself on top of the reveal
  // that was still running (review 2026-07-26).
  it("does not replay the stagger when the launch conversation's id is learned", () => {
    const rows = userRows(3);
    const view = open(rows, null);

    view.switchTo("c-1", rows); // same messages, id now known

    expect(view.rows().every((el) => el.style.animation === "")).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  // …but opening a DIFFERENT chat from an untouched one goes null → "c-…" too,
  // and that one really is a switch: it brings other messages with it.
  it("still staggers a real switch away from the untouched launch conversation", () => {
    const view = open([], null); // the greeting stack, nothing to animate yet

    view.switchTo("c-2", userRows(3));

    expect(view.rows()[0].style.animation).toContain("fadeRise");
  });

  it("is a hard no-op under reduced motion", () => {
    setMotionEnabled(false);
    const view = open(userRows(3), "c-1");

    view.switchTo("c-2", userRows(3));

    expect(view.rows().every((el) => el.style.animation === "")).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  // Two dozen 38ms intervals can be in flight after a switch, and nothing used
  // to stop them: the thread unmounts whenever a surface replaces the chat
  // column, leaving them ticking over detached nodes for the life of the webview.
  it("cancels the row scrambles when the thread goes away", () => {
    const view = open(userRows(3), "c-1");
    view.switchTo("c-2", userRows(3));
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    view.unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});
