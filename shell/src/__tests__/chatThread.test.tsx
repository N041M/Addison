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

import { describe, it, expect, vi, beforeAll } from "vitest";
import { render } from "@testing-library/react";
import { ChatThread } from "../components/ChatThread";
import type { DisplayMessage } from "../types/ui";

vi.mock("../components/MermaidDiagram", () => ({
  MermaidDiagram: () => null,
}));

// jsdom implements no layout, so it ships no scrollIntoView; the thread's
// keep-the-newest-line-in-view effect calls it on every render.
beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
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
