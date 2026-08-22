// Highlight → Ask / Explain (KNOWN-GAPS, "Feature suggestions judged
// 2026-08-09", first bullet). Two things are worth testing here and one of them
// is not the popover:
//
//   * WHAT GOES INTO THE COMPOSER. The seed is the whole feature — a markdown
//     blockquote of exactly what was highlighted, a blank line, and then either
//     nothing (Ask) or the canned question (Explain). Seeding the raw text
//     instead would look right on screen and quietly stop being a quote the
//     moment the selection is two lines long or starts with a `#`.
//   * WHEN NOTHING IS OFFERED. A selection that is empty, that lies on text
//     still arriving, or that runs across two messages must produce no panel at
//     all. Each of those is a way of quoting something the person did not point
//     at, and each one fails silently: the panel appears, the quote is wrong,
//     and nothing in the app says so.
//
// jsdom implements Selection and Range for real, so the selections below are
// genuine ranges rather than a mocked API. Two things it does NOT implement are
// stubbed at the top: `Range.getBoundingClientRect` (the panel's anchor) and
// `Element.scrollIntoView` (the thread's follow effect). It also never fires
// `selectionchange` of its own accord, so the tests that exercise that path
// dispatch the event themselves — which is exactly what the keyboard route
// (Shift+arrows, no mouseup) looks like to the component.

import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { ChatThread, resetThreadStaggerForTests } from "../components/ChatThread";
import { EXPLAIN_QUESTION, quoteForComposer } from "../components/SelectionAsk";
import { installScrambleClickHandler, setMotionEnabled } from "../lib/scramble";
import type { DisplayMessage } from "../types/ui";

vi.mock("../components/MermaidDiagram", () => ({
  MermaidDiagram: () => null,
}));

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
  // jsdom has no layout, so a Range cannot measure itself. The panel only uses
  // the rect to place itself, which nothing here asserts.
  Range.prototype.getBoundingClientRect = () =>
    ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }) as DOMRect;
});

beforeEach(() => {
  resetThreadStaggerForTests();
  window.getSelection()?.removeAllRanges();
});

afterEach(() => {
  setMotionEnabled(true);
});

const ANSWER: DisplayMessage = {
  id: "a1",
  role: "assistant",
  content: "The folder is where files land when you download them.",
  pending: false,
};

function renderThread(messages: DisplayMessage[], extra = {}) {
  const onSuggestion = vi.fn();
  const view = render(
    <ChatThread
      messages={messages}
      onRetry={() => {}}
      retryAvailable={false}
      onRewindTo={() => {}}
      onSuggestion={onSuggestion}
      conversationKey="c-1"
      {...extra}
    />,
  );
  return { ...view, onSuggestion };
}

/** Highlight the whole of one element, the way a drag across it would. */
function selectContentsOf(el: Element) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  return range;
}

/** Let go of the mouse — the moment the panel decides whether to appear. */
function finishDrag() {
  fireEvent.mouseUp(document.body);
}

function panel(container: HTMLElement) {
  return container.querySelector("[data-testid='selection-ask']");
}

/** The first text node under an element — where a real caret would land. */
function firstTextNode(el: Node): Text {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const node = walker.nextNode();
  if (!node) throw new Error("no text node under the element");
  return node as Text;
}

function action(label: string): HTMLButtonElement | null {
  const buttons = Array.from(
    document.querySelectorAll("[data-testid='selection-ask'] button"),
  ) as HTMLButtonElement[];
  return buttons.find((b) => b.textContent === label) ?? null;
}

// ---------------------------------------------------------------------------
// The quote itself, in isolation: the one string the whole feature produces.
// ---------------------------------------------------------------------------
describe("the quote written into the composer", () => {
  it("prefixes every line, and leaves a blank line to write in", () => {
    expect(quoteForComposer("one line")).toBe("> one line\n\n");
  });

  it("quotes every line of a multi-line selection", () => {
    expect(quoteForComposer("line one\nline two\n\nline four")).toBe(
      "> line one\n> line two\n>\n> line four\n\n",
    );
  });

  it("keeps the indentation of code but drops a sloppy drag's edges", () => {
    expect(quoteForComposer("\n    if (x) {\n      go();\n    }\n  \n")).toBe(
      ">     if (x) {\n>       go();\n>     }\n\n",
    );
  });

  it("normalises Windows line endings rather than quoting a stray carriage return", () => {
    expect(quoteForComposer("a\r\nb")).toBe("> a\n> b\n\n");
  });

  // The prefix is added, never sliced into: an emoji is several code units and a
  // right-to-left run reorders on screen without reordering in the string.
  it("leaves emoji and right-to-left text exactly as they were", () => {
    expect(quoteForComposer("done 🎉👍🏽\nمرحبا بالعالم")).toBe(
      "> done 🎉👍🏽\n> مرحبا بالعالم\n\n",
    );
  });
});

// ---------------------------------------------------------------------------
// The panel: when it is offered, and what each button seeds.
// ---------------------------------------------------------------------------
describe("selecting text in a settled message", () => {
  it("offers Ask and Explain", () => {
    const { container } = renderThread([ANSWER]);

    selectContentsOf(container.querySelector("[data-ask-selectable]")!);
    finishDrag();

    expect(panel(container)).not.toBeNull();
    expect(action("Ask")).not.toBeNull();
    expect(action("Explain")).not.toBeNull();
  });

  it("Ask seeds the composer with the quote and nothing else", () => {
    const { container, onSuggestion } = renderThread([ANSWER]);
    selectContentsOf(container.querySelector("[data-ask-selectable]")!);
    finishDrag();

    fireEvent.click(action("Ask")!);

    expect(onSuggestion).toHaveBeenCalledWith(
      "> The folder is where files land when you download them.\n\n",
    );
    // The panel goes with the press: the quote is in the composer now.
    expect(panel(container)).toBeNull();
  });

  it("Explain seeds the same quote plus the question, and still does not send", () => {
    const { container, onSuggestion } = renderThread([ANSWER]);
    selectContentsOf(container.querySelector("[data-ask-selectable]")!);
    finishDrag();

    fireEvent.click(action("Explain")!);

    expect(onSuggestion).toHaveBeenCalledWith(
      `> The folder is where files land when you download them.\n\n${EXPLAIN_QUESTION}`,
    );
    expect(EXPLAIN_QUESTION).toBe("What does this mean, in plain language?");
  });

  it("quotes every line when the selection spans several", () => {
    const multi: DisplayMessage = {
      id: "u1",
      role: "user",
      content: "Move the invoices\nthen tell me\n\nwhat you did",
    };
    const { container, onSuggestion } = renderThread([multi]);
    selectContentsOf(container.querySelector("[data-ask-selectable]")!);
    finishDrag();

    fireEvent.click(action("Ask")!);

    expect(onSuggestion).toHaveBeenCalledWith(
      "> Move the invoices\n> then tell me\n>\n> what you did\n\n",
    );
  });

  // A rendered answer is elements, not one text node, and the code inside a
  // fence is the most likely thing anybody highlights to ask about. Quoting code
  // into a blockquote is fine — the indentation is what has to survive.
  it("quotes code out of a rendered fence with its indentation intact", () => {
    const withCode: DisplayMessage = {
      id: "a3",
      role: "assistant",
      content: "Here it is:\n\n```js\nif (x) {\n  go();\n}\n```",
      pending: false,
    };
    const { container, onSuggestion } = renderThread([withCode]);
    const code = container.querySelector("pre code")!;

    selectContentsOf(code);
    finishDrag();
    fireEvent.click(action("Ask")!);

    expect(onSuggestion).toHaveBeenCalledWith("> if (x) {\n>   go();\n> }\n\n");
  });

  // The thread re-renders constantly while another answer streams. The panel
  // holds the STRING it captured, not a node, so a rerender under it cannot
  // change what gets quoted.
  it("keeps quoting what was highlighted when the thread rerenders around it", () => {
    const later: DisplayMessage = { id: "a9", role: "assistant", content: "", pending: true };
    const { container, rerender, onSuggestion } = renderThread([ANSWER]);
    selectContentsOf(container.querySelector("[data-ask-selectable]")!);
    finishDrag();

    rerender(
      <ChatThread
        messages={[ANSWER, later]}
        onRetry={() => {}}
        retryAvailable={false}
        onRewindTo={() => {}}
        onSuggestion={onSuggestion}
        conversationKey="c-1"
        streamMessageId="a9"
        streamDisplay="Anot#er ans%er"
      />,
    );

    expect(panel(container)).not.toBeNull();
    fireEvent.click(action("Ask")!);
    expect(onSuggestion).toHaveBeenCalledWith(
      "> The folder is where files land when you download them.\n\n",
    );
  });

  // A selection made with Shift+arrows never sends a mouseup. Nothing parallel
  // was built for it: `selectionchange` is the browser's own announcement, and
  // the panel listens to that too.
  it("is offered for a keyboard selection, which sends no mouseup", () => {
    const { container } = renderThread([ANSWER]);

    selectContentsOf(container.querySelector("[data-ask-selectable]")!);
    fireEvent(document, new Event("selectionchange"));

    expect(panel(container)).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The three silences. Each one is a way of quoting text the person did not
// point at, and each fails without a symptom.
// ---------------------------------------------------------------------------
describe("what is deliberately not offered", () => {
  it("offers nothing when nothing is selected", () => {
    const { container } = renderThread([ANSWER]);

    finishDrag();

    expect(panel(container)).toBeNull();
  });

  it("offers nothing for a collapsed caret inside a message", () => {
    const { container } = renderThread([ANSWER]);
    const body = container.querySelector("[data-ask-selectable]")!;
    const range = document.createRange();
    range.setStart(firstTextNode(body), 2);
    range.collapse(true);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);

    finishDrag();

    expect(panel(container)).toBeNull();
  });

  // A row that is still arriving carries random glyphs in its tail. Membership
  // is read from the row's own state (`data-ask-selectable`), never guessed at
  // from the characters.
  it("offers nothing on a message that has not arrived yet", () => {
    const pending: DisplayMessage = { id: "a2", role: "assistant", content: "", pending: true };
    const { container } = renderThread([pending]);
    expect(container.querySelector("[data-ask-selectable]")).toBeNull();

    selectContentsOf(container.querySelector("[data-msg-text]")!);
    finishDrag();

    expect(panel(container)).toBeNull();
  });

  it("offers nothing on a message still resolving out of the scramble", () => {
    const { container } = renderThread([ANSWER], {
      streamMessageId: "a1",
      streamDisplay: "The f%lder is wh#re",
    });
    expect(container.querySelector("[data-ask-selectable]")).toBeNull();

    selectContentsOf(container.firstElementChild!.children[0]!);
    finishDrag();

    expect(panel(container)).toBeNull();
  });

  it("offers nothing for a selection that runs across two messages", () => {
    const second: DisplayMessage = { id: "u2", role: "user", content: "Thanks, that helps." };
    const { container } = renderThread([ANSWER, second]);
    const bodies = container.querySelectorAll("[data-ask-selectable]");
    const range = document.createRange();
    range.setStart(firstTextNode(bodies[0]), 0);
    range.setEnd(firstTextNode(bodies[1]), 5);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    // The selection really does hold text from both messages…
    expect(selection.toString().length).toBeGreaterThan(0);

    finishDrag();

    // …and is offered nothing, because a quote of "both, partly" is not a quote.
    expect(panel(container)).toBeNull();
  });

  it("offers nothing for a selection on the sender label", () => {
    const { container } = renderThread([ANSWER]);

    selectContentsOf(container.querySelector("[data-scramble-live]")!);
    finishDrag();

    expect(panel(container)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Getting rid of it. A popover that lingers over the thread is furniture.
// ---------------------------------------------------------------------------
describe("dismissal", () => {
  function offered() {
    const view = renderThread([ANSWER]);
    selectContentsOf(view.container.querySelector("[data-ask-selectable]")!);
    finishDrag();
    expect(panel(view.container)).not.toBeNull();
    return view;
  }

  it("goes away on Escape", () => {
    const { container } = offered();

    fireEvent.keyDown(document, { key: "Escape" });

    expect(panel(container)).toBeNull();
  });

  it("goes away when the selection collapses", () => {
    const { container } = offered();

    window.getSelection()!.removeAllRanges();
    fireEvent(document, new Event("selectionchange"));

    expect(panel(container)).toBeNull();
  });

  it("goes away when a new press starts somewhere else", () => {
    const { container } = offered();

    fireEvent.mouseDown(document.body);

    expect(panel(container)).toBeNull();
  });

  it("goes away when the thread is scrolled", () => {
    const { container } = offered();

    fireEvent.scroll(container.firstElementChild!);

    expect(panel(container)).toBeNull();
  });

  it("goes away when another conversation is opened", () => {
    const { container, rerender, onSuggestion } = offered();

    rerender(
      <ChatThread
        messages={[ANSWER]}
        onRetry={() => {}}
        retryAvailable={false}
        onRewindTo={() => {}}
        onSuggestion={onSuggestion}
        conversationKey="c-2"
      />,
    );

    expect(panel(container)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The thread scrambles some of its own text when it is clicked
// (installScrambleClickHandler). The panel sits inside that thread, and a press
// on Ask must not rewrite anything.
//
// THE GUARD IS THE SELECTOR, not a stopPropagation: the handler only ever
// scrambles a LEAF carrying `data-scramble`, `data-scramble-live` or
// `data-scramble-click`. The panel carries none of the three and sits under no
// element that does, so the handler walks up from the button and finds nothing.
// That is asserted directly below, because it is the property that would break
// if the panel were ever moved inside a row's label.
// ---------------------------------------------------------------------------
describe("the click-to-scramble handler", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is not reached by a press on Ask", () => {
    const uninstall = installScrambleClickHandler(document);
    try {
      const { container, onSuggestion } = renderThread([ANSWER]);
      const body = container.querySelector("[data-ask-selectable]")!;
      selectContentsOf(body);
      finishDrag();
      const ask = action("Ask")!;

      // The guard, stated: no scramble target anywhere above the button.
      expect(ask.closest("[data-scramble],[data-scramble-live],[data-scramble-click]")).toBeNull();

      fireEvent.click(ask);
      vi.advanceTimersByTime(200);

      expect(onSuggestion).toHaveBeenCalledTimes(1);
      // Nothing was rewritten, and no resolve loop is ticking.
      expect(body.textContent).toBe(ANSWER.content);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      uninstall();
    }
  });

  // The other half of the same interaction: the message BODY is not a scramble
  // target either (only the sender label is), so letting go of a drag inside it
  // cannot start a resolve loop over the text that was just highlighted.
  it("does not fire when a drag ends inside a message body", () => {
    const uninstall = installScrambleClickHandler(document);
    try {
      const { container } = renderThread([ANSWER]);
      const body = container.querySelector("[data-ask-selectable]")!;
      selectContentsOf(body);
      finishDrag();

      fireEvent.click(body);
      vi.advanceTimersByTime(200);

      expect(body.textContent).toBe(ANSWER.content);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      uninstall();
    }
  });
});
