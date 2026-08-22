// The one rendering rule an answer that formats itself as it arrives depends on
// (owner request 2026-08-21, reworked 2026-08-22, replacing the 2026-07-26
// shape of the same rule): NO SCRAMBLED GLYPH EVER REACHES THE MARKDOWN PARSER.
// The parse stops at the last newline behind the resolved edge — the prefix the
// frame and the true text still agree on — so what is left over, the part the
// glyphs are in, stays plain pre-wrap text.
//
// This is not a style preference. The scramble writes random glyphs into the
// text 26 times a second, and markdown is structural: a stray `#` at the start
// of a frame becomes an <h1> for 38ms, a stray `-` becomes a list item, and the
// answer visibly reflows under the reader's eyes while they are trying to read
// it. The guard used to be one `&& !revealing` in ChatThread and is now the
// common-prefix gate in `lib/streamMarkdown.ts`; either way it was proven
// necessary the hard way — with the old guard reverted, the entire suite still
// passed, so nothing but this file and streamMarkdown.test.tsx is watching it.
// The frames used below share only a short prefix with the answer, which is why
// nothing settles in them and the whole frame is on screen verbatim.
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
      onContinue={() => {}}
      retryAvailable={false}
      onRewindTo={() => {}}
      {...extra}
    />,
  );
}

describe("a message whose text is still resolving", () => {
  it("parses nothing the scramble is still standing in", () => {
    // A frame mid-reveal: the leading `#` is present, as it is in the real text,
    // and the frame diverges from the answer inside the very first block — so
    // the resolved edge is five characters in, no block is behind it, and the
    // whole frame is still tail.
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
        onContinue={() => {}}
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
      onContinue() {},
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
      onContinue() {},
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
      onContinue() {},
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

  // -------------------------------------------------------------------------
  // KNOWN-BUGS P2 #3 — a pending approval card invisible behind "Working…".
  //
  // The consent card renders in the thread's FOOTER whenever the widget rail is
  // hidden, and the effect above deliberately ignores footer changes (a fresh
  // fragment every render). So the card was appended below the fold of a
  // container whose scrollbar is hidden, with nothing on screen to say it was
  // there — and since a blocked turn sends no further messages and no chunks,
  // nothing scrolled again. The sighting fits exactly: four minutes of
  // "Working…", no card, no timeout, and Stop revealed it because Stop rewrites
  // `messages` (pending → settled), which IS in the dependency list.
  //
  // `attention` is the narrow signal that fixes it without giving the rest of
  // the footer the same power — which is what the test below it holds.
  // -------------------------------------------------------------------------
  const consentProps = {
    messages: ROWS,
    onRetry() {},
    onContinue() {},
    retryAvailable: false,
    onRewindTo() {},
  };

  it("scrolls the consent card itself into view when one arrives", () => {
    const { container, rerender } = render(
      <ChatThread {...consentProps} footer={<div>work</div>} attention={null} />,
    );
    scrollTo(list(container), 4);
    scrollIntoView.mockClear();

    rerender(
      <ChatThread
        {...consentProps}
        footer={
          <>
            <div>work</div>
            {/* PermissionCard's own container attribute. */}
            <div data-consent-card="">Allow</div>
            {/* What can follow it: with the rail open inline, the card rides at the
                TOP and the widget list runs on below. Scrolling to the FOOT of the
                thread would push the question off the top of the viewport. */}
            <div style={{ height: 4000 }}>widgets</div>
          </>
        }
        attention="arm_automation:"
      />,
    );
    expect(scrollIntoView).toHaveBeenCalled();
    const target = scrollIntoView.mock.instances[0] as HTMLElement;
    expect(target.hasAttribute("data-consent-card")).toBe(true);
  });

  it("brings a reader who has scrolled up back to the card", () => {
    // Being scrolled up is not consent to miss a blocking question — the one
    // exception to "never jail the reader", alongside a message they just sent.
    const { container, rerender } = render(<ChatThread {...consentProps} attention={null} />);
    scrollTo(list(container), 900);
    scrollIntoView.mockClear();

    rerender(<ChatThread {...consentProps} attention="spy_tool:" />);
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("does not re-scroll while the same card stays up", () => {
    const { container, rerender } = render(
      <ChatThread {...consentProps} attention="spy_tool:" />,
    );
    scrollTo(list(container), 900); // they scrolled away deliberately, card in view
    scrollIntoView.mockClear();

    // Unrelated re-renders with the SAME question on screen must leave them be.
    rerender(<ChatThread {...consentProps} attention="spy_tool:" footer={<div>a</div>} />);
    rerender(<ChatThread {...consentProps} attention="spy_tool:" footer={<div>b</div>} />);
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("scrolls again when a keyword card is re-asked after a mistyped code", () => {
    // Same tool, new ask: the attempts count is in the key precisely so this
    // counts as an arrival rather than the same card re-rendering.
    const { container, rerender } = render(
      <ChatThread {...consentProps} attention="arm_automation:3" />,
    );
    scrollTo(list(container), 900);
    scrollIntoView.mockClear();

    rerender(<ChatThread {...consentProps} attention="arm_automation:2" />);
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("still ignores an ordinary footer rebuild", () => {
    // The guard the fix must not cost: App rebuilds the footer on every render.
    const { rerender } = render(
      <ChatThread {...consentProps} footer={<div>one</div>} attention={null} />,
    );
    scrollIntoView.mockClear();

    rerender(<ChatThread {...consentProps} footer={<div>two</div>} attention={null} />);
    expect(scrollIntoView).not.toHaveBeenCalled();
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
    const props = {
      messages,
      onRetry() {},
      retryAvailable: false,
      onContinue() {},
      onRewindTo() {},
      conversationKey,
    };
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
